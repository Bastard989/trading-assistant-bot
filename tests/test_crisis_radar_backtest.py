from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scripts.backtest_crisis_radar import execute_payload, parse_payload
from trading_bot.crisis_radar.backtest import (
    BacktestSample,
    ScenarioEvent,
    SignalPoint,
    build_labeled_samples,
    walk_forward_calibrate,
)
from trading_bot.crisis_radar.catalog import METHODOLOGY_CODE, METHODOLOGY_VERSION
from trading_bot.crisis_radar.domain import Observation
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


START = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _sample(index: int, score: str, outcome: bool, *, horizon_days: int = 7) -> BacktestSample:
    predicted_at = START + timedelta(days=index * 30)
    event_at = predicted_at + timedelta(days=3) if outcome else None
    return BacktestSample(
        scenario_code="global_recession",
        predicted_at=predicted_at,
        horizon_end=predicted_at + timedelta(days=horizon_days),
        signal_score=Decimal(score),
        outcome=outcome,
        event_at=event_at,
    )


def test_event_labels_only_future_starts_inside_the_requested_horizon() -> None:
    signals = (
        SignalPoint("global_recession", START, Decimal("0.2")),
        SignalPoint("global_recession", START + timedelta(days=30), Decimal("0.8")),
    )
    events = (
        ScenarioEvent(
            "global_recession",
            START - timedelta(days=5),
            START + timedelta(days=5),
        ),
        ScenarioEvent("global_recession", START + timedelta(days=45)),
    )

    samples = build_labeled_samples(signals, events, horizon=timedelta(days=20))

    assert samples[0].outcome is False
    assert samples[1].outcome is True
    assert samples[1].event_at == START + timedelta(days=45)


def test_probability_is_hidden_until_resolved_training_is_sufficient() -> None:
    samples = tuple(
        _sample(index, "0.1" if index % 2 == 0 else "0.9", index % 2 == 1)
        for index in range(8)
    )

    result = walk_forward_calibrate(
        samples,
        min_training_samples=20,
        min_bin_samples=3,
    )

    assert result.metrics.scored_count == 0
    assert result.metrics.coverage == Decimal("0")
    assert result.metrics.brier_score is None
    assert all(item.calibrated_probability is None for item in result.predictions)
    assert all(item.confidence == "insufficient" for item in result.predictions)


def test_walk_forward_uses_only_horizons_resolved_before_each_prediction() -> None:
    samples = tuple(
        _sample(index, "0.1" if index % 2 == 0 else "0.9", index % 2 == 1)
        for index in range(24)
    )

    result = walk_forward_calibrate(
        samples,
        min_training_samples=8,
        min_bin_samples=3,
        min_positive_samples=2,
        min_negative_samples=2,
    )

    assert result.predictions[0].training_sample_count == 0
    assert result.predictions[8].training_sample_count == 8
    for prediction in result.predictions:
        if prediction.latest_training_horizon_end is not None:
            assert prediction.latest_training_horizon_end <= prediction.sample.predicted_at
    assert result.metrics.scored_count == 16
    assert result.metrics.coverage == Decimal("0.666667")
    assert result.metrics.brier_score is not None
    assert result.metrics.baseline_brier_score is not None
    assert result.metrics.brier_score < result.metrics.baseline_brier_score
    assert result.metrics.precision == Decimal("1.000000")
    assert result.metrics.recall == Decimal("1.000000")
    assert result.metrics.false_alert_rate == Decimal("0.000000")
    assert result.metrics.average_lead_days == Decimal("3.0000")
    available_low = [
        item.calibrated_probability
        for item in result.predictions
        if item.calibrated_probability is not None and item.sample.signal_score == Decimal("0.1")
    ]
    available_high = [
        item.calibrated_probability
        for item in result.predictions
        if item.calibrated_probability is not None and item.sample.signal_score == Decimal("0.9")
    ]
    assert max(available_low) < min(available_high)
    assert result.calibration_bins


def test_backtest_rejects_mixed_scenarios_and_invalid_event_contracts() -> None:
    with pytest.raises(ValueError, match="exactly one scenario"):
        build_labeled_samples(
            (
                SignalPoint("global_recession", START, Decimal("0.2")),
                SignalPoint("financial_stress", START + timedelta(days=1), Decimal("0.3")),
            ),
            (),
            horizon=timedelta(days=30),
        )
    with pytest.raises(ValueError, match="outcome must agree"):
        BacktestSample(
            scenario_code="global_recession",
            predicted_at=START,
            horizon_end=START + timedelta(days=30),
            signal_score=Decimal("0.2"),
            outcome=True,
        )


def test_as_of_inputs_use_only_releases_available_at_that_time(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "as-of.sqlite3"))
    CrisisRadarService(repository).bootstrap()

    def save(value: str, observed_at: datetime, released_at: datetime, vintage: str) -> None:
        repository.save_observation(
            Observation(
                indicator_code="vix",
                source_code="fred",
                value=Decimal(value),
                unit="index_points",
                observed_at=observed_at,
                released_at=released_at,
                fetched_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
                vintage=vintage,
            )
        )

    save("20", START, START + timedelta(days=1), "initial")
    save("40", START, START + timedelta(days=20), "revision")
    save("30", START + timedelta(days=31), START + timedelta(days=32), "next")

    early = repository.analysis_inputs_as_of(
        METHODOLOGY_CODE, METHODOLOGY_VERSION, as_of=START + timedelta(days=10)
    )
    revised = repository.analysis_inputs_as_of(
        METHODOLOGY_CODE, METHODOLOGY_VERSION, as_of=START + timedelta(days=25)
    )
    future = repository.analysis_inputs_as_of(
        METHODOLOGY_CODE, METHODOLOGY_VERSION, as_of=START + timedelta(days=40)
    )

    assert next(item for item in early if item.observation.indicator_code == "vix").observation.value == Decimal("20")
    assert next(item for item in revised if item.observation.indicator_code == "vix").observation.value == Decimal("40")
    assert next(item for item in future if item.observation.indicator_code == "vix").observation.value == Decimal("30")
    assert repository.recent_indicator_values_as_of(
        "vix", as_of=START + timedelta(days=25), limit=5
    ) == [Decimal("40")]


def test_backtest_result_is_persisted_with_auditable_leakage_fields(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "persisted.sqlite3"))
    CrisisRadarService(repository).bootstrap()
    samples = tuple(
        _sample(index, "0.1" if index % 2 == 0 else "0.9", index % 2 == 1)
        for index in range(24)
    )
    result = walk_forward_calibrate(
        samples,
        min_training_samples=8,
        min_bin_samples=3,
        min_positive_samples=2,
        min_negative_samples=2,
    )

    run_id = repository.save_backtest_result(
        result,
        methodology_code=METHODOLOGY_CODE,
        methodology_version=METHODOLOGY_VERSION,
        parameters={"bin_count": 5, "minimum_training_samples": 8},
        completed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    payload = repository.backtest_run_payload(run_id)

    assert payload is not None
    assert payload["scenario_code"] == "global_recession"
    assert payload["methodology"] == {
        "code": METHODOLOGY_CODE,
        "version": METHODOLOGY_VERSION,
    }
    assert payload["metrics"]["sample_count"] == 24
    assert payload["metrics"]["scored_count"] == 16
    assert len(payload["predictions"]) == 24
    assert payload["calibration_bins"]
    for prediction in payload["predictions"]:
        if prediction["latest_training_horizon_end"] is not None:
            assert prediction["latest_training_horizon_end"] <= prediction["predicted_at"]
    calibration = CrisisRadarService(repository).scenario_calibration("global_recession")
    assert calibration is not None
    assert calibration["ready"] is False
    assert calibration["probability"] is None
    assert calibration["reason"] == "no_completed_backtest"
    assert calibration["historical_backtest"] is None
    assert repository.latest_backtest_payload("global_recession")["run_id"] == run_id
    assert (
        repository.latest_backtest_payload(
            "global_recession",
            methodology_code=METHODOLOGY_CODE,
            methodology_version=METHODOLOGY_VERSION,
            require_provenance=True,
        )
        is None
    )


def test_cli_payload_contract_returns_probability_only_after_calibration() -> None:
    signals = []
    events = []
    for index in range(24):
        as_of = START + timedelta(days=index * 30)
        signals.append(
            {
                "as_of": as_of.isoformat(),
                "score": "0.1" if index % 2 == 0 else "0.9",
            }
        )
        if index % 2 == 1:
            events.append({"started_at": (as_of + timedelta(days=3)).isoformat()})
    payload = {
        "scenario_code": "global_recession",
        "horizon_days": 7,
        "signals": signals,
        "events": events,
        "calibration": {
            "min_training_samples": 8,
            "min_bin_samples": 3,
            "min_positive_samples": 2,
            "min_negative_samples": 2,
        },
    }

    result = execute_payload(payload)

    assert result["run_id"] is None
    assert result["engine"] == "walk-forward-v1"
    assert result["latest_probability"] is not None
    assert result["latest_confidence"] in {"low", "medium", "high"}
    assert result["metrics"]["scored_count"] == 16


def test_cli_payload_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_payload(
            {
                "scenario_code": "global_recession",
                "horizon_days": 30,
                "signals": [{"as_of": "2020-01-01T00:00:00", "score": "0.5"}],
                "events": [],
            }
        )
