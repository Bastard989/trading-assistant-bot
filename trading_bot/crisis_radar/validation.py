from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from trading_bot.crisis_radar.backtest import BacktestMetrics


MIN_INDEPENDENT_EVENTS = 30
MIN_HOLDOUT_EVENTS = 5
MIN_RECALL = Decimal("0.20")
MAX_FALSE_ALERT_RATE = Decimal("0.50")
MIN_SCORED_COVERAGE = Decimal("0.50")


@dataclass(frozen=True)
class CalibrationGateResult:
    passed: bool
    reasons: tuple[str, ...]
    criteria: dict[str, bool]


def evaluate_calibration_gate(
    metrics: BacktestMetrics,
    *,
    holdout_event_count: int,
    sensitivity_stable: bool,
    region_holdout_passed: bool,
    crisis_holdout_passed: bool,
) -> CalibrationGateResult:
    criteria = {
        "minimum_independent_events": metrics.positive_event_count >= MIN_INDEPENDENT_EVENTS,
        "minimum_holdout_events": holdout_event_count >= MIN_HOLDOUT_EVENTS,
        "beats_base_rate_brier": bool(
            metrics.brier_score is not None
            and metrics.baseline_brier_score is not None
            and metrics.brier_score < metrics.baseline_brier_score
        ),
        "recall_floor": bool(metrics.recall is not None and metrics.recall >= MIN_RECALL),
        "false_alert_ceiling": bool(
            metrics.false_alert_rate is not None
            and metrics.false_alert_rate <= MAX_FALSE_ALERT_RATE
        ),
        "scored_coverage_floor": metrics.coverage >= MIN_SCORED_COVERAGE,
        "sensitivity_stable": sensitivity_stable,
        "region_holdout_passed": region_holdout_passed,
        "crisis_holdout_passed": crisis_holdout_passed,
    }
    reasons = tuple(code for code, passed in criteria.items() if not passed)
    return CalibrationGateResult(not reasons, reasons, criteria)


def evaluate_stored_calibration_gate(metrics: dict, validation: dict | None) -> CalibrationGateResult:
    validation = validation or {}
    def decimal(key: str) -> Decimal | None:
        return None if metrics.get(key) is None else Decimal(str(metrics[key]))
    parsed = BacktestMetrics(
        sample_count=int(metrics.get("sample_count") or 0),
        scored_count=int(metrics.get("scored_count") or 0),
        positive_count=int(metrics.get("positive_count") or 0),
        positive_event_count=int(metrics.get("positive_event_count") or 0),
        coverage=decimal("coverage") or Decimal("0"),
        brier_score=decimal("brier_score"),
        baseline_brier_score=decimal("baseline_brier_score"),
        log_loss=decimal("log_loss"),
        precision=decimal("precision"),
        recall=decimal("recall"),
        false_alert_rate=decimal("false_alert_rate"),
        average_lead_days=decimal("average_lead_days"),
    )
    return evaluate_calibration_gate(
        parsed,
        holdout_event_count=int(validation.get("holdout_event_count") or 0),
        sensitivity_stable=bool(validation.get("sensitivity_stable")),
        region_holdout_passed=bool(validation.get("region_holdout_passed")),
        crisis_holdout_passed=bool(validation.get("crisis_holdout_passed")),
    )


def threshold_sensitivity(
    scores: Iterable[Decimal],
    outcomes: Iterable[bool],
    *,
    thresholds: tuple[Decimal, ...] = (
        Decimal("0.40"),
        Decimal("0.45"),
        Decimal("0.50"),
        Decimal("0.55"),
        Decimal("0.60"),
    ),
) -> tuple[dict, ...]:
    pairs = tuple(zip(scores, outcomes, strict=True))
    rows = []
    for threshold in thresholds:
        true_positive = sum(score >= threshold and outcome for score, outcome in pairs)
        false_positive = sum(score >= threshold and not outcome for score, outcome in pairs)
        false_negative = sum(score < threshold and outcome for score, outcome in pairs)
        precision = (
            None
            if true_positive + false_positive == 0
            else Decimal(true_positive) / Decimal(true_positive + false_positive)
        )
        recall = (
            None
            if true_positive + false_negative == 0
            else Decimal(true_positive) / Decimal(true_positive + false_negative)
        )
        rows.append(
            {
                "threshold": str(threshold),
                "precision": None if precision is None else str(precision.quantize(Decimal("0.0001"))),
                "recall": None if recall is None else str(recall.quantize(Decimal("0.0001"))),
                "false_alert_count": false_positive,
            }
        )
    return tuple(rows)


def ablation_report(
    *,
    full_model_brier: Decimal,
    without_channel_brier: dict[str, Decimal],
) -> tuple[dict, ...]:
    return tuple(
        {
            "channel": channel,
            "brier_without_channel": str(value),
            "delta_vs_full": str((value - full_model_brier).quantize(Decimal("0.000001"))),
            "adds_out_of_sample_value": value > full_model_brier,
        }
        for channel, value in sorted(without_channel_brier.items())
    )
