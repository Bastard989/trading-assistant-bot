from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.crisis_radar.domain import (
    IndicatorBand,
    IndicatorThresholds,
    Observation,
    RiskDirection,
)
from trading_bot.crisis_radar.stability import stabilize_indicator_state
from trading_bot.crisis_radar.states import build_indicator_state


NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def _state(value: str, thresholds: IndicatorThresholds):
    observation = Observation(
        indicator_code="test",
        source_code="fred",
        value=Decimal(value),
        unit="index",
        observed_at=NOW,
        released_at=NOW,
        fetched_at=NOW,
    )
    return build_indicator_state(
        observation,
        group_code="test_group",
        thresholds=thresholds,
        max_staleness_seconds=86400,
        snapshot_at=NOW,
    )


def _higher_thresholds() -> IndicatorThresholds:
    return IndicatorThresholds(
        warning=Decimal("1"),
        danger=Decimal("2"),
        critical=Decimal("3"),
        direction=RiskDirection.HIGHER_IS_WORSE,
    )


def test_noncritical_escalation_requires_distinct_confirming_observations() -> None:
    thresholds = _higher_thresholds()
    pending = stabilize_indicator_state(
        _state("2.5", thresholds),
        previous_band=IndicatorBand.NORMAL,
        recent_values=[Decimal("2.5")],
        thresholds=thresholds,
        confirmation_points=2,
    )
    confirmed = stabilize_indicator_state(
        _state("2.5", thresholds),
        previous_band=IndicatorBand.NORMAL,
        recent_values=[Decimal("2.5"), Decimal("2.2")],
        thresholds=thresholds,
        confirmation_points=2,
    )

    assert pending.band is IndicatorBand.NORMAL
    assert pending.raw_band is IndicatorBand.DANGER
    assert pending.persistence_count == 1
    assert pending.stress_score == Decimal("0.2499")
    assert confirmed.band is IndicatorBand.DANGER
    assert confirmed.persistence_count == 2


def test_critical_crossing_is_immediate() -> None:
    thresholds = _higher_thresholds()
    state = stabilize_indicator_state(
        _state("3.5", thresholds),
        previous_band=IndicatorBand.NORMAL,
        recent_values=[Decimal("3.5")],
        thresholds=thresholds,
        confirmation_points=2,
    )
    assert state.band is IndicatorBand.CRITICAL
    assert state.persistence_count == 1


def test_hysteresis_holds_higher_is_worse_until_recovery_margin_is_cleared() -> None:
    thresholds = _higher_thresholds()
    held = stabilize_indicator_state(
        _state("0.95", thresholds),
        previous_band=IndicatorBand.WARNING,
        recent_values=[Decimal("0.95")],
        thresholds=thresholds,
        confirmation_points=2,
    )
    recovered = stabilize_indicator_state(
        _state("0.80", thresholds),
        previous_band=IndicatorBand.WARNING,
        recent_values=[Decimal("0.80")],
        thresholds=thresholds,
        confirmation_points=2,
    )

    assert held.band is IndicatorBand.WARNING
    assert held.held_by_hysteresis is True
    assert held.stress_score == Decimal("0.25")
    assert recovered.band is IndicatorBand.NORMAL
    assert recovered.held_by_hysteresis is False


def test_hysteresis_supports_lower_is_worse_thresholds() -> None:
    thresholds = IndicatorThresholds(
        warning=Decimal("-10"),
        danger=Decimal("-20"),
        critical=Decimal("-30"),
        direction=RiskDirection.LOWER_IS_WORSE,
    )
    held = stabilize_indicator_state(
        _state("-9.5", thresholds),
        previous_band=IndicatorBand.WARNING,
        recent_values=[Decimal("-9.5")],
        thresholds=thresholds,
        confirmation_points=2,
    )
    recovered = stabilize_indicator_state(
        _state("-8", thresholds),
        previous_band=IndicatorBand.WARNING,
        recent_values=[Decimal("-8")],
        thresholds=thresholds,
        confirmation_points=2,
    )
    assert held.band is IndicatorBand.WARNING
    assert held.held_by_hysteresis is True
    assert recovered.band is IndicatorBand.NORMAL
