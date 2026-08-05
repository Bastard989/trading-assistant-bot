from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_EVEN

from trading_bot.crisis_radar.domain import (
    DataFreshness,
    CoverageAssessment,
    CoverageStatus,
    FreshnessPolicy,
    GroupState,
    IndicatorBand,
    IndicatorState,
    IndicatorThresholds,
    MarketOverview,
    MarketStage,
    Observation,
    RiskDirection,
)
from trading_bot.crisis_radar.thresholds import evaluate_threshold


ZERO = Decimal("0")
ONE = Decimal("1")


def evaluate_freshness(
    released_at: datetime,
    *,
    as_of: datetime,
    policy: FreshnessPolicy,
) -> DataFreshness:
    if released_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("freshness timestamps must be timezone-aware")
    age = as_of - released_at
    if age < timedelta(0):
        raise ValueError("release time cannot be in the future")
    if age <= policy.max_age:
        return DataFreshness.FRESH
    delayed_seconds = policy.max_age.total_seconds() * float(policy.delayed_multiplier)
    if age.total_seconds() <= delayed_seconds:
        return DataFreshness.DELAYED
    return DataFreshness.STALE


def _clamp(value: Decimal) -> Decimal:
    return max(ZERO, min(ONE, value))


def _stress_score(value: Decimal, thresholds: IndicatorThresholds) -> Decimal:
    risk_value = abs(value - thresholds.reference) if thresholds.direction is RiskDirection.TWO_SIDED else value
    if thresholds.direction is RiskDirection.LOWER_IS_WORSE:
        if risk_value > thresholds.warning:
            span = max(thresholds.reference - thresholds.warning, Decimal("0.00000001"))
            score = (thresholds.reference - risk_value) / span * Decimal("0.25")
        elif risk_value > thresholds.danger:
            score = Decimal("0.25") + (thresholds.warning - risk_value) / (
                thresholds.warning - thresholds.danger
            ) * Decimal("0.25")
        elif risk_value > thresholds.critical:
            score = Decimal("0.50") + (thresholds.danger - risk_value) / (
                thresholds.danger - thresholds.critical
            ) * Decimal("0.25")
        else:
            span = max(thresholds.danger - thresholds.critical, Decimal("0.00000001"))
            score = Decimal("0.75") + (thresholds.critical - risk_value) / span * Decimal("0.25")
    elif risk_value < thresholds.warning:
        span = max(thresholds.warning - thresholds.reference, Decimal("0.00000001"))
        score = (risk_value - thresholds.reference) / span * Decimal("0.25")
    elif risk_value < thresholds.danger:
        score = Decimal("0.25") + (risk_value - thresholds.warning) / (
            thresholds.danger - thresholds.warning
        ) * Decimal("0.25")
    elif risk_value < thresholds.critical:
        score = Decimal("0.50") + (risk_value - thresholds.danger) / (
            thresholds.critical - thresholds.danger
        ) * Decimal("0.25")
    else:
        span = max(thresholds.critical - thresholds.danger, Decimal("0.00000001"))
        score = Decimal("0.75") + (risk_value - thresholds.critical) / span * Decimal("0.25")
    return _clamp(score).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def build_indicator_state(
    observation: Observation,
    *,
    group_code: str,
    thresholds: IndicatorThresholds,
    max_staleness_seconds: int,
    snapshot_at: datetime,
) -> IndicatorState:
    evaluation = evaluate_threshold(observation.value, thresholds)
    freshness = evaluate_freshness(
        observation.released_at,
        as_of=snapshot_at,
        policy=FreshnessPolicy(timedelta(seconds=max_staleness_seconds)),
    )
    return IndicatorState(
        indicator_code=observation.indicator_code,
        group_code=group_code,
        band=evaluation.band,
        stress_score=_stress_score(observation.value, thresholds),
        distance_to_next=evaluation.distance_to_next,
        freshness=freshness,
        value=observation.value,
        unit=observation.unit,
        snapshot_at=snapshot_at,
        observation=observation,
        raw_band=evaluation.band,
    )


def _band_from_score(score: Decimal) -> IndicatorBand:
    if score >= Decimal("0.75"):
        return IndicatorBand.CRITICAL
    if score >= Decimal("0.50"):
        return IndicatorBand.DANGER
    if score >= Decimal("0.25"):
        return IndicatorBand.WARNING
    return IndicatorBand.NORMAL


def aggregate_groups(states: list[IndicatorState]) -> list[GroupState]:
    grouped: dict[str, list[IndicatorState]] = defaultdict(list)
    for state in states:
        if state.freshness is not DataFreshness.STALE:
            grouped[state.group_code].append(state)
    result: list[GroupState] = []
    for group_code, items in sorted(grouped.items()):
        ordered = sorted(items, key=lambda item: item.stress_score, reverse=True)
        strongest = ordered[0].stress_score
        breadth = sum(item.stress_score for item in ordered) / Decimal(len(ordered))
        score = (strongest * Decimal("0.7") + breadth * Decimal("0.3")).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_EVEN
        )
        result.append(
            GroupState(
                group_code=group_code,
                band=_band_from_score(score),
                stress_score=score,
                indicator_count=len(items),
                worsening_count=sum(item.band is not IndicatorBand.NORMAL for item in items),
                contributors=tuple(item.indicator_code for item in ordered[:5]),
            )
        )
    return result


def build_market_overview(
    states: list[IndicatorState],
    *,
    snapshot_at: datetime,
    coverage: CoverageAssessment | None = None,
) -> MarketOverview:
    groups = aggregate_groups(states)
    warning = sum(group.band in {IndicatorBand.WARNING, IndicatorBand.DANGER, IndicatorBand.CRITICAL} for group in groups)
    danger = sum(group.band in {IndicatorBand.DANGER, IndicatorBand.CRITICAL} for group in groups)
    critical = sum(group.band is IndicatorBand.CRITICAL for group in groups)
    if critical >= 3:
        calculated_stage = MarketStage.CRISIS
    elif critical >= 2 or danger >= 3:
        calculated_stage = MarketStage.CONFIRMATION
    elif danger >= 2 or warning >= 3:
        calculated_stage = MarketStage.WARNING
    elif warning >= 1:
        calculated_stage = MarketStage.TENSION
    else:
        calculated_stage = MarketStage.STABLE
    stage = (
        MarketStage.INSUFFICIENT_DATA
        if coverage is not None and coverage.status is CoverageStatus.INSUFFICIENT_DATA
        else calculated_stage
    )

    labels_ru = {
        "labor": "рынок труда",
        "credit": "кредит",
        "market_stress": "рыночный стресс",
        "real_economy": "реальная экономика",
        "inflation_commodities": "инфляция и сырьё",
        "euro_financial_stress": "финансовый стресс еврозоны",
        "euro_growth": "экономический рост еврозоны",
        "crypto_leverage": "криптовалютные плечи",
        "crypto_price_stress": "ценовой стресс крипторынка",
        "equity_market_stress": "стресс рынка акций США",
        "rates_liquidity": "ставки и ликвидность США",
        "us_financial_conditions": "финансовые условия США",
        "china_growth": "экономический рост Китая",
        "global_growth": "мировой экономический рост",
        "global_credit_cycle": "глобальный кредитный цикл",
        "global_leading_cycle": "опережающий цикл G20",
        "china_leading_cycle": "опережающий цикл Китая",
    }
    active = [labels_ru.get(group.group_code, group.group_code) for group in groups if group.band is not IndicatorBand.NORMAL]
    if coverage is not None and coverage.status is CoverageStatus.INSUFFICIENT_DATA:
        missing = ", ".join(coverage.missing_required_groups) or "обязательные каналы"
        explanation_ru = (
            "Недостаточно свежих данных для оценки рынка. "
            f"Недоступны или просрочены: {missing}."
        )
        missing_en = ", ".join(coverage.missing_required_groups) or "required channels"
        explanation_en = (
            "There is not enough fresh data to assess the market. "
            f"Unavailable or stale: {missing_en}."
        )
    elif active:
        explanation_ru = f"Ухудшение отмечено в группах: {', '.join(active)}. Стадия основана на совместном подтверждении."
        explanation_en = "Deterioration is present in: " + ", ".join(
            group.group_code for group in groups if group.band is not IndicatorBand.NORMAL
        ) + ". The stage reflects cross-group confirmation."
    else:
        explanation_ru = "Независимые группы пока не дают подтверждённого кризисного сочетания."
        explanation_en = "Independent groups do not currently confirm a crisis combination."
    return MarketOverview(
        stage=stage,
        calculated_stage=calculated_stage,
        snapshot_at=snapshot_at,
        groups=tuple(groups),
        active_group_count=warning,
        warning_group_count=warning,
        danger_group_count=danger,
        critical_group_count=critical,
        explanation_ru=explanation_ru,
        explanation_en=explanation_en,
        coverage=coverage,
    )
