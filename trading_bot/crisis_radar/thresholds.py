from __future__ import annotations

from decimal import Decimal, InvalidOperation

from trading_bot.crisis_radar.domain import (
    IndicatorBand,
    IndicatorThresholds,
    RiskDirection,
    ThresholdEvaluation,
)


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("indicator value must be a number") from exc
    if not result.is_finite():
        raise ValueError("indicator value must be finite")
    return result


def evaluate_threshold(value: object, thresholds: IndicatorThresholds) -> ThresholdEvaluation:
    """Evaluate an indicator without converting the result into a probability."""

    raw_value = _decimal(value)
    evaluated_value = raw_value
    if thresholds.direction is RiskDirection.TWO_SIDED:
        evaluated_value = abs(raw_value - thresholds.reference)

    if thresholds.direction is RiskDirection.LOWER_IS_WORSE:
        if evaluated_value <= thresholds.critical:
            return ThresholdEvaluation(IndicatorBand.CRITICAL, None, evaluated_value)
        if evaluated_value <= thresholds.danger:
            distance = evaluated_value - thresholds.critical
            return ThresholdEvaluation(IndicatorBand.DANGER, distance, evaluated_value)
        if evaluated_value <= thresholds.warning:
            distance = evaluated_value - thresholds.danger
            return ThresholdEvaluation(IndicatorBand.WARNING, distance, evaluated_value)
        distance = evaluated_value - thresholds.warning
        return ThresholdEvaluation(IndicatorBand.NORMAL, distance, evaluated_value)

    if evaluated_value >= thresholds.critical:
        return ThresholdEvaluation(IndicatorBand.CRITICAL, None, evaluated_value)
    if evaluated_value >= thresholds.danger:
        distance = thresholds.critical - evaluated_value
        return ThresholdEvaluation(IndicatorBand.DANGER, distance, evaluated_value)
    if evaluated_value >= thresholds.warning:
        distance = thresholds.danger - evaluated_value
        return ThresholdEvaluation(IndicatorBand.WARNING, distance, evaluated_value)
    distance = thresholds.warning - evaluated_value
    return ThresholdEvaluation(IndicatorBand.NORMAL, distance, evaluated_value)
