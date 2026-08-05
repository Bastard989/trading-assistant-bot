from decimal import Decimal

from trading_bot.crisis_radar.backtest import BacktestMetrics
from trading_bot.crisis_radar.validation import (
    ablation_report,
    evaluate_calibration_gate,
    threshold_sensitivity,
)


def metrics(events: int = 30) -> BacktestMetrics:
    return BacktestMetrics(
        sample_count=200,
        scored_count=160,
        positive_count=40,
        positive_event_count=events,
        coverage=Decimal("0.80"),
        brier_score=Decimal("0.12"),
        baseline_brier_score=Decimal("0.16"),
        log_loss=Decimal("0.4"),
        precision=Decimal("0.60"),
        recall=Decimal("0.50"),
        false_alert_rate=Decimal("0.30"),
        average_lead_days=Decimal("10"),
    )


def test_promotion_gate_requires_all_out_of_sample_evidence() -> None:
    passed = evaluate_calibration_gate(
        metrics(),
        holdout_event_count=5,
        sensitivity_stable=True,
        region_holdout_passed=True,
        crisis_holdout_passed=True,
    )
    assert passed.passed is True

    blocked = evaluate_calibration_gate(
        metrics(events=29),
        holdout_event_count=4,
        sensitivity_stable=False,
        region_holdout_passed=False,
        crisis_holdout_passed=False,
    )
    assert blocked.passed is False
    assert "minimum_independent_events" in blocked.reasons
    assert "minimum_holdout_events" in blocked.reasons


def test_sensitivity_and_ablation_are_deterministic() -> None:
    sensitivity = threshold_sensitivity(
        (Decimal("0.2"), Decimal("0.6"), Decimal("0.8")),
        (False, True, False),
        thresholds=(Decimal("0.5"), Decimal("0.7")),
    )
    assert sensitivity[0]["recall"] == "1.0000"
    assert sensitivity[1]["recall"] == "0.0000"
    report = ablation_report(
        full_model_brier=Decimal("0.12"),
        without_channel_brier={"news": Decimal("0.13"), "trend": Decimal("0.11")},
    )
    assert report[0]["channel"] == "news"
    assert report[0]["adds_out_of_sample_value"] is True
    assert report[1]["adds_out_of_sample_value"] is False
