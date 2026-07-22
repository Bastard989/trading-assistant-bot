from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_bot.crisis_radar.catalog import (
    FRED_INDICATORS,
    METHODOLOGY_CODE,
    METHODOLOGY_VERSION,
    bootstrap_starter_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.domain import (
    DataFreshness,
    FreshnessPolicy,
    IndicatorBand,
    IndicatorThresholds,
    MarketStage,
    Observation,
    RiskDirection,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.states import (
    build_indicator_state,
    build_market_overview,
    evaluate_freshness,
)
from trading_bot.db import Database


NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def _observation(code: str, value: str, *, age_days: int = 1, unit: str = "index") -> Observation:
    released_at = NOW - timedelta(days=age_days)
    return Observation(
        indicator_code=code,
        source_code="fred",
        value=Decimal(value),
        unit=unit,
        observed_at=released_at,
        released_at=released_at,
        fetched_at=NOW,
        vintage=released_at.date().isoformat(),
    )


def _state(code: str, group: str, value: str, thresholds: IndicatorThresholds):
    return build_indicator_state(
        _observation(code, value),
        group_code=group,
        thresholds=thresholds,
        max_staleness_seconds=4 * 86400,
        snapshot_at=NOW,
    )


def test_starter_catalog_is_versioned_and_idempotent(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "catalog.sqlite3"))
    first = bootstrap_starter_catalog(repository)
    second = bootstrap_starter_catalog(repository)

    assert first == second
    assert first["indicator_count"] == 23
    assert len(methodology_checksum()) == 64
    with repository.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM cr_sources").fetchone()[0] == 11
        assert connection.execute("SELECT count(*) FROM cr_indicator_definitions").fetchone()[0] == 31
        assert connection.execute(
            "SELECT count(*) FROM cr_indicator_definitions WHERE enabled = 1"
        ).fetchone()[0] == 23
        assert connection.execute("SELECT count(*) FROM cr_threshold_sets").fetchone()[0] == 23


def test_methodology_checksum_cannot_change_in_place(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "methodology.sqlite3"))
    bootstrap_starter_catalog(repository)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        repository.register_methodology(
            METHODOLOGY_CODE,
            METHODOLOGY_VERSION,
            checksum="tampered",
            effective_from="2026-07-20T00:00:00+00:00",
        )


def test_freshness_has_fresh_delayed_and_stale_states() -> None:
    policy = FreshnessPolicy(timedelta(days=4))
    assert evaluate_freshness(NOW - timedelta(days=2), as_of=NOW, policy=policy) is DataFreshness.FRESH
    assert evaluate_freshness(NOW - timedelta(days=5), as_of=NOW, policy=policy) is DataFreshness.DELAYED
    assert evaluate_freshness(NOW - timedelta(days=7), as_of=NOW, policy=policy) is DataFreshness.STALE


def test_indicator_state_preserves_band_distance_and_near_threshold_stress() -> None:
    thresholds = IndicatorThresholds(
        warning=Decimal("4.5"),
        danger=Decimal("6"),
        critical=Decimal("8"),
        direction=RiskDirection.HIGHER_IS_WORSE,
    )
    state = _state("us_hy_oas", "credit", "4.2", thresholds)
    assert state.band is IndicatorBand.NORMAL
    assert state.distance_to_next == Decimal("0.3")
    assert Decimal("0.20") < state.stress_score < Decimal("0.25")


def test_market_stage_requires_cross_group_confirmation() -> None:
    thresholds = IndicatorThresholds(
        warning=Decimal("1"),
        danger=Decimal("2"),
        critical=Decimal("3"),
        direction=RiskDirection.HIGHER_IS_WORSE,
    )
    one_group = [_state("credit_a", "credit", "2.5", thresholds)]
    assert build_market_overview(one_group, snapshot_at=NOW).stage is MarketStage.TENSION

    warning = [
        _state("labor_a", "labor", "2.5", thresholds),
        _state("credit_a", "credit", "2.5", thresholds),
    ]
    assert build_market_overview(warning, snapshot_at=NOW).stage is MarketStage.WARNING

    confirmation = [
        _state("labor_a", "labor", "3.5", thresholds),
        _state("credit_a", "credit", "3.5", thresholds),
    ]
    assert build_market_overview(confirmation, snapshot_at=NOW).stage is MarketStage.CONFIRMATION


def test_stale_indicator_is_excluded_from_group_confirmation() -> None:
    thresholds = IndicatorThresholds(
        warning=Decimal("1"),
        danger=Decimal("2"),
        critical=Decimal("3"),
        direction=RiskDirection.HIGHER_IS_WORSE,
    )
    fresh = _state("labor_a", "labor", "3.5", thresholds)
    stale = build_indicator_state(
        _observation("credit_a", "3.5", age_days=20),
        group_code="credit",
        thresholds=thresholds,
        max_staleness_seconds=4 * 86400,
        snapshot_at=NOW,
    )
    overview = build_market_overview([fresh, stale], snapshot_at=NOW)
    assert overview.stage is MarketStage.TENSION
    assert [group.group_code for group in overview.groups] == ["labor"]


def test_latest_inputs_and_analysis_snapshot_round_trip(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "round-trip.sqlite3"))
    catalog = bootstrap_starter_catalog(repository)
    for seed, value in zip(FRED_INDICATORS[:3], ("0.10", "5.0", "28"), strict=True):
        repository.save_observation(
            _observation(seed.code, value, unit=seed.unit)
        )

    inputs = repository.latest_analysis_inputs(METHODOLOGY_CODE, METHODOLOGY_VERSION)
    states = [
        build_indicator_state(
            item.observation,
            group_code=item.group_code,
            thresholds=item.thresholds,
            max_staleness_seconds=item.max_staleness_seconds,
            snapshot_at=NOW,
        )
        for item in inputs
    ]
    overview = build_market_overview(states, snapshot_at=NOW)
    repository.save_analysis_snapshot(states, overview, methodology_id=int(catalog["methodology_id"]))
    repository.save_analysis_snapshot(states, overview, methodology_id=int(catalog["methodology_id"]))

    with repository.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM cr_indicator_states").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM cr_group_states").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM cr_market_snapshots").fetchone()[0] == 1
