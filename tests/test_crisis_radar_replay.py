from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scripts.replay_crisis_radar import execute_replay
from scripts.replay_crisis_radar import _calibration_acceptable
from trading_bot.crisis_radar.backtest import ScenarioEvent, SignalPoint, build_labeled_samples
from trading_bot.crisis_radar.backtest import BacktestMetrics
from trading_bot.crisis_radar.catalog import METHODOLOGY_CODE, METHODOLOGY_VERSION
from trading_bot.crisis_radar.domain import Observation
from trading_bot.crisis_radar.event_catalog import EventCatalogVersion, HistoricalEventLabel
from trading_bot.crisis_radar.replay import replay_scenario
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


UTC = timezone.utc
START = datetime(2020, 1, 1, tzinfo=UTC)
SOURCE_URL = "https://example.gov/official-methodology"


def _catalog(*, version: str = "official-v1", value: str = "baseline") -> EventCatalogVersion:
    return EventCatalogVersion(
        scenario_code="global_recession",
        version=version,
        source_name="Official statistical authority",
        source_url=SOURCE_URL,
        definition={"target": value, "precision": "year"},
        limitations=("Annual labels do not identify an exact daily onset.",),
        effective_from=datetime(2026, 7, 21, tzinfo=UTC),
        labels=(
            HistoricalEventLabel(
                code="global-recession-2020",
                started_at=datetime(2020, 1, 1, tzinfo=UTC),
                ended_at=datetime(2020, 12, 31, 23, 59, 59, tzinfo=UTC),
                start_precision="year",
                end_precision="year",
                region_code="GLOBAL",
                source_url=SOURCE_URL,
            ),
        ),
    )


def test_event_catalog_is_checksum_versioned_and_immutable(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "catalog.sqlite3"))
    first = _catalog()

    catalog_id = repository.register_event_catalog(first)
    assert repository.register_event_catalog(first) == catalog_id
    payload = repository.event_catalog_payload("global_recession")

    assert payload is not None
    assert payload["checksum"] == first.checksum
    assert payload["labels"][0]["start_precision"] == "year"
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        repository.register_event_catalog(_catalog(value="changed"))


def test_event_catalog_rejects_overlapping_labels() -> None:
    with pytest.raises(ValueError, match="must not overlap"):
        EventCatalogVersion(
            scenario_code="financial_stress",
            version="overlap-v1",
            source_name="Official authority",
            source_url=SOURCE_URL,
            definition={},
            limitations=(),
            effective_from=datetime(2026, 7, 21, tzinfo=UTC),
            labels=(
                HistoricalEventLabel(
                    "one",
                    START,
                    START + timedelta(days=10),
                    "day",
                    "day",
                    "GLOBAL",
                    SOURCE_URL,
                ),
                HistoricalEventLabel(
                    "two",
                    START + timedelta(days=5),
                    START + timedelta(days=15),
                    "day",
                    "day",
                    "GLOBAL",
                    SOURCE_URL,
                ),
            ),
        )


def test_replay_is_reproducible_as_of_and_does_not_write_live_snapshots(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "replay.sqlite3"))
    CrisisRadarService(repository).bootstrap()
    repository.save_observation(
        Observation(
            indicator_code="vix",
            source_code="fred",
            value=Decimal("20"),
            unit="index_points",
            observed_at=START,
            released_at=START,
            fetched_at=datetime(2026, 7, 21, tzinfo=UTC),
            vintage="initial",
        )
    )
    repository.save_observation(
        Observation(
            indicator_code="vix",
            source_code="fred",
            value=Decimal("45"),
            unit="index_points",
            observed_at=START,
            released_at=START + timedelta(days=90),
            fetched_at=datetime(2026, 7, 21, tzinfo=UTC),
            vintage="future-revision",
        )
    )

    options = dict(
        started_at=START + timedelta(days=1),
        ended_at=START + timedelta(days=30),
        step=timedelta(days=7),
        minimum_coverage=Decimal("0"),
    )
    first = replay_scenario(repository, "financial_stress", **options)
    second = replay_scenario(repository, "financial_stress", **options)

    assert first.checksum == second.checksum
    assert all(item.latest_released_at <= item.signal_at for item in first.signals)
    assert all(item.signal_score < Decimal("0.75") for item in first.signals)
    run_id = repository.save_replay_result(first)
    stored = repository.replay_run_payload(run_id)
    assert stored is not None
    assert stored["result_checksum"] == first.checksum
    assert repository.replay_signal_points(run_id)
    with repository.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM cr_market_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM cr_alert_events").fetchone()[0] == 0


def test_onset_labels_exclude_active_events_and_right_censored_horizons() -> None:
    event = ScenarioEvent(
        "global_recession",
        START + timedelta(days=10),
        START + timedelta(days=20),
    )
    signals = tuple(
        SignalPoint("global_recession", START + timedelta(days=day), Decimal("0.5"))
        for day in (0, 12, 25, 35)
    )

    samples = build_labeled_samples(
        signals,
        (event,),
        horizon=timedelta(days=15),
        coverage_end=START + timedelta(days=40),
        exclude_active_events=True,
    )

    assert [item.predicted_at for item in samples] == [START, START + timedelta(days=25)]
    assert samples[0].outcome is True
    assert samples[1].outcome is False


def test_replay_requires_known_methodology(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "missing.sqlite3"))
    with pytest.raises(LookupError, match="replay methodology"):
        repository.save_replay_result(
            replay_scenario(
                repository,
                "global_recession",
                started_at=START,
                ended_at=START,
                step=timedelta(days=1),
                methodology_code=METHODOLOGY_CODE,
                methodology_version=METHODOLOGY_VERSION,
            )
        )


def test_replay_cli_path_persists_backtest_lineage_without_live_probability(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "vertical.sqlite3"))
    CrisisRadarService(repository).bootstrap()
    for offset in range(0, 900, 90):
        observed_at = datetime(2008, 1, 1, tzinfo=UTC) + timedelta(days=offset)
        repository.save_observation(
            Observation(
                indicator_code="us_real_gdp_qoq",
                source_code="bea",
                value=Decimal("2"),
                unit="percent_annualized",
                observed_at=observed_at,
                released_at=observed_at,
                fetched_at=datetime(2026, 7, 21, tzinfo=UTC),
                vintage=f"quarter-{offset}",
            )
        )

    payload = execute_replay(
        repository,
        scenario_code="global_recession",
        started_at=datetime(2008, 1, 1, tzinfo=UTC),
        ended_at=datetime(2010, 1, 1, tzinfo=UTC),
        cadence_days=30,
        horizon_days=365,
        minimum_coverage=Decimal("0.10"),
        persist=True,
    )

    assert payload["replay_run_id"] is not None
    assert payload["backtest_run_id"] is not None
    assert payload["live_probability"] is None
    assert payload["event_catalog"]["event_count"] == 5
    backtest = repository.backtest_run_payload(payload["backtest_run_id"])
    assert backtest is not None
    assert backtest["lineage"]["replay_run_id"] == payload["replay_run_id"]
    assert backtest["lineage"]["event_catalog_version"].startswith("world-bank")


def test_calibration_must_beat_baseline_and_detect_multiple_events() -> None:
    common = {
        "sample_count": 100,
        "scored_count": 80,
        "positive_count": 20,
        "coverage": Decimal("0.8"),
        "log_loss": Decimal("0.5"),
        "precision": Decimal("0.6"),
        "recall": Decimal("0.5"),
        "false_alert_rate": Decimal("0.1"),
        "average_lead_days": Decimal("12"),
    }
    baseline_equal = BacktestMetrics(
        positive_event_count=8,
        brier_score=Decimal("0.16"),
        baseline_brier_score=Decimal("0.16"),
        **common,
    )
    too_few_events = BacktestMetrics(
        positive_event_count=2,
        brier_score=Decimal("0.12"),
        baseline_brier_score=Decimal("0.16"),
        **common,
    )
    acceptable = BacktestMetrics(
        positive_event_count=30,
        brier_score=Decimal("0.12"),
        baseline_brier_score=Decimal("0.16"),
        **common,
    )

    assert _calibration_acceptable(baseline_equal) is False
    assert _calibration_acceptable(too_few_events) is False
    validation = {
        "holdout_event_count": 5,
        "sensitivity_stable": True,
        "region_holdout_passed": True,
        "crisis_holdout_passed": True,
    }
    assert _calibration_acceptable(acceptable) is False
    assert _calibration_acceptable(acceptable, validation) is True
