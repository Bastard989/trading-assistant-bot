from decimal import Decimal

import pytest

from trading_bot.crisis_radar.domain import IndicatorBand, IndicatorThresholds, RiskDirection
from trading_bot.crisis_radar.thresholds import evaluate_threshold


def test_higher_is_worse_boundaries_and_distance() -> None:
    thresholds = IndicatorThresholds(
        warning=Decimal("4.5"),
        danger=Decimal("6"),
        critical=Decimal("8"),
        direction=RiskDirection.HIGHER_IS_WORSE,
    )

    normal = evaluate_threshold("2.71", thresholds)
    warning = evaluate_threshold("4.5", thresholds)
    danger = evaluate_threshold("6", thresholds)
    critical = evaluate_threshold("8", thresholds)

    assert (normal.band, normal.distance_to_next) == (IndicatorBand.NORMAL, Decimal("1.79"))
    assert (warning.band, warning.distance_to_next) == (IndicatorBand.WARNING, Decimal("1.5"))
    assert (danger.band, danger.distance_to_next) == (IndicatorBand.DANGER, Decimal("2"))
    assert (critical.band, critical.distance_to_next) == (IndicatorBand.CRITICAL, None)


def test_lower_is_worse_boundaries_and_distance() -> None:
    thresholds = IndicatorThresholds(
        warning=Decimal("50"),
        danger=Decimal("40"),
        critical=Decimal("25"),
        direction=RiskDirection.LOWER_IS_WORSE,
    )

    assert evaluate_threshold(55, thresholds).band is IndicatorBand.NORMAL
    assert evaluate_threshold(50, thresholds).band is IndicatorBand.WARNING
    result = evaluate_threshold(30, thresholds)
    assert result.band is IndicatorBand.DANGER
    assert result.distance_to_next == Decimal("5")
    assert evaluate_threshold(25, thresholds).band is IndicatorBand.CRITICAL


def test_two_sided_threshold_uses_distance_from_reference() -> None:
    thresholds = IndicatorThresholds(
        warning=Decimal("1"),
        danger=Decimal("2"),
        critical=Decimal("3"),
        direction=RiskDirection.TWO_SIDED,
        reference=Decimal("5"),
    )

    result = evaluate_threshold("2.5", thresholds)
    assert result.band is IndicatorBand.DANGER
    assert result.evaluated_value == Decimal("2.5")
    assert result.distance_to_next == Decimal("0.5")


@pytest.mark.parametrize("value", ["NaN", "Infinity", float("-inf"), "not-a-number"])
def test_non_finite_or_invalid_indicator_values_are_rejected(value) -> None:
    thresholds = IndicatorThresholds(
        warning=Decimal("1"),
        danger=Decimal("2"),
        critical=Decimal("3"),
        direction=RiskDirection.HIGHER_IS_WORSE,
    )
    with pytest.raises(ValueError, match="number|finite"):
        evaluate_threshold(value, thresholds)


def test_threshold_order_is_validated_for_each_direction() -> None:
    with pytest.raises(ValueError, match="stricter"):
        IndicatorThresholds(
            warning=Decimal("8"),
            danger=Decimal("6"),
            critical=Decimal("4.5"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        )
    with pytest.raises(ValueError, match="stricter"):
        IndicatorThresholds(
            warning=Decimal("25"),
            danger=Decimal("40"),
            critical=Decimal("50"),
            direction=RiskDirection.LOWER_IS_WORSE,
        )
