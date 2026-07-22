from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from trading_bot.crisis_radar.domain import (
    IndicatorBand,
    IndicatorState,
    IndicatorThresholds,
    RiskDirection,
)
from trading_bot.crisis_radar.thresholds import evaluate_threshold


@dataclass(frozen=True)
class StabilityPolicy:
    confirmation_points: int = 2
    recovery_fraction: Decimal = Decimal("0.10")
    critical_immediate: bool = True
    alert_cooldown_seconds: int = 6 * 3600

    def __post_init__(self) -> None:
        if self.confirmation_points < 1:
            raise ValueError("confirmation_points must be positive")
        if not Decimal("0") < self.recovery_fraction < Decimal("1"):
            raise ValueError("recovery_fraction must be between zero and one")
        if self.alert_cooldown_seconds < 0:
            raise ValueError("alert_cooldown_seconds must not be negative")


STABILITY_POLICY = StabilityPolicy()


_BAND_RANK = {
    IndicatorBand.NORMAL: 0,
    IndicatorBand.WARNING: 1,
    IndicatorBand.DANGER: 2,
    IndicatorBand.CRITICAL: 3,
}
_RANK_BAND = {rank: band for band, rank in _BAND_RANK.items()}


def _score_for_band(score: Decimal, band: IndicatorBand) -> Decimal:
    bounds = {
        IndicatorBand.NORMAL: (Decimal("0"), Decimal("0.2499")),
        IndicatorBand.WARNING: (Decimal("0.25"), Decimal("0.4999")),
        IndicatorBand.DANGER: (Decimal("0.50"), Decimal("0.7499")),
        IndicatorBand.CRITICAL: (Decimal("0.75"), Decimal("1")),
    }
    lower, upper = bounds[band]
    return max(lower, min(upper, score))


def _recovery_boundary(
    band: IndicatorBand, thresholds: IndicatorThresholds, fraction: Decimal
) -> Decimal:
    if band is IndicatorBand.WARNING:
        threshold = thresholds.warning
        safer = thresholds.danger
    elif band is IndicatorBand.DANGER:
        threshold = thresholds.danger
        safer = thresholds.warning
    elif band is IndicatorBand.CRITICAL:
        threshold = thresholds.critical
        safer = thresholds.danger
    else:
        return thresholds.warning
    distance = abs(threshold - safer) * fraction
    if thresholds.direction is RiskDirection.LOWER_IS_WORSE:
        return threshold + distance
    return threshold - distance


def apply_hysteresis(
    value: Decimal,
    *,
    raw_band: IndicatorBand,
    previous_band: IndicatorBand,
    thresholds: IndicatorThresholds,
    recovery_fraction: Decimal,
) -> tuple[IndicatorBand, bool]:
    if _BAND_RANK[raw_band] >= _BAND_RANK[previous_band]:
        return raw_band, False
    evaluated = evaluate_threshold(value, thresholds).evaluated_value
    boundary = _recovery_boundary(previous_band, thresholds, recovery_fraction)
    still_inside = (
        evaluated <= boundary
        if thresholds.direction is RiskDirection.LOWER_IS_WORSE
        else evaluated >= boundary
    )
    return (previous_band, True) if still_inside else (raw_band, False)


def consecutive_band_count(
    values: list[Decimal], target: IndicatorBand, thresholds: IndicatorThresholds
) -> int:
    target_rank = _BAND_RANK[target]
    count = 0
    for value in values:
        if _BAND_RANK[evaluate_threshold(value, thresholds).band] < target_rank:
            break
        count += 1
    return count


def stabilize_indicator_state(
    state: IndicatorState,
    *,
    previous_band: IndicatorBand | None,
    recent_values: list[Decimal],
    thresholds: IndicatorThresholds,
    confirmation_points: int,
    policy: StabilityPolicy = STABILITY_POLICY,
) -> IndicatorState:
    raw_band = state.band
    previous = previous_band or IndicatorBand.NORMAL
    candidate, held = apply_hysteresis(
        state.value,
        raw_band=raw_band,
        previous_band=previous,
        thresholds=thresholds,
        recovery_fraction=policy.recovery_fraction,
    )
    persistence_count = consecutive_band_count(recent_values, candidate, thresholds)
    escalating = _BAND_RANK[candidate] > _BAND_RANK[previous]
    immediate = policy.critical_immediate and candidate is IndicatorBand.CRITICAL
    if escalating and not immediate and persistence_count < confirmation_points:
        confirmed = previous
        for rank in range(_BAND_RANK[candidate] - 1, _BAND_RANK[previous], -1):
            fallback = _RANK_BAND[rank]
            if consecutive_band_count(recent_values, fallback, thresholds) >= confirmation_points:
                confirmed = fallback
                break
        candidate = confirmed
    return replace(
        state,
        band=candidate,
        stress_score=_score_for_band(state.stress_score, candidate),
        raw_band=raw_band,
        persistence_count=persistence_count,
        confirmation_required=confirmation_points,
        held_by_hysteresis=held,
    )
