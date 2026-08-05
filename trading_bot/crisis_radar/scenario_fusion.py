from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from trading_bot.crisis_radar.domain import (
    CoverageStatus,
    GroupState,
    IndicatorBand,
    ScenarioConfidence,
    ScenarioState,
    ScenarioStatus,
)
from trading_bot.crisis_radar.scenarios import ScenarioDefinition
from trading_bot.crisis_radar.trends import ContagionFeatures, IndicatorFeatures


FUSION_VERSION = "scenario-fusion-v1"
ZERO = Decimal("0")
ONE = Decimal("1")


DEPENDENCY_CLUSTERS = {
    "labor": "real_economy",
    "real_economy": "real_economy",
    "euro_growth": "real_economy",
    "china_growth": "real_economy",
    "global_growth": "real_economy",
    "canada_growth": "real_economy",
    "uk_growth": "real_economy",
    "japan_growth": "real_economy",
    "korea_growth": "real_economy",
    "india_growth": "real_economy",
    "brazil_growth": "real_economy",
    "mexico_growth": "real_economy",
    "china_leading_cycle": "leading_cycle",
    "global_leading_cycle": "leading_cycle",
    "canada_leading_cycle": "leading_cycle",
    "uk_leading_cycle": "leading_cycle",
    "japan_leading_cycle": "leading_cycle",
    "korea_leading_cycle": "leading_cycle",
    "india_leading_cycle": "leading_cycle",
    "brazil_leading_cycle": "leading_cycle",
    "mexico_leading_cycle": "leading_cycle",
    "credit": "credit_funding",
    "global_credit_cycle": "credit_funding",
    "rates_liquidity": "credit_funding",
    "us_financial_conditions": "financial_conditions",
    "euro_financial_stress": "financial_conditions",
    "market_stress": "market_prices",
    "equity_market_stress": "market_prices",
    "crypto_price_stress": "crypto_prices",
    "crypto_leverage": "crypto_leverage",
    "inflation_commodities": "commodities",
}


@dataclass(frozen=True)
class FusionComponent:
    score: Decimal
    maximum: Decimal
    evidence: tuple[dict, ...]


@dataclass(frozen=True)
class ScenarioFusionState:
    code: str
    snapshot_at: datetime
    status: ScenarioStatus
    confidence: ScenarioConfidence
    strength: Decimal
    reliability: Decimal
    numeric: FusionComponent
    trend: FusionComponent
    contagion: FusionComponent
    news: FusionComponent
    independent_numeric_clusters: int
    anchor_active: bool
    explanation_ru: str
    explanation_en: str
    input_checksum: str

    def as_scenario_state(self, horizon: str) -> ScenarioState:
        numeric_evidence = tuple(
            (str(item["group_code"]), IndicatorBand(str(item["band"])))
            for item in self.numeric.evidence
            if item.get("group_code") and item.get("band")
        )
        return ScenarioState(
            code=self.code,
            status=self.status,
            confidence=self.confidence,
            horizon=horizon,
            active_group_count=len(numeric_evidence),
            evidence=numeric_evidence,
            explanation_ru=self.explanation_ru,
            explanation_en=self.explanation_en,
        )


_BAND_SCORE = {
    IndicatorBand.NORMAL: ZERO,
    IndicatorBand.WARNING: Decimal("0.34"),
    IndicatorBand.DANGER: Decimal("0.67"),
    IndicatorBand.CRITICAL: ONE,
}


def _clamp(value: Decimal, low: Decimal = ZERO, high: Decimal = ONE) -> Decimal:
    return min(high, max(low, value))


def _numeric_component(
    definition: ScenarioDefinition,
    groups: tuple[GroupState, ...],
) -> tuple[FusionComponent, int, bool]:
    by_code = {item.group_code: item for item in groups}
    selected = [by_code[code] for code in definition.group_codes if code in by_code]
    active = [item for item in selected if item.band is not IndicatorBand.NORMAL]
    by_cluster: dict[str, list[GroupState]] = {}
    for item in active:
        cluster = DEPENDENCY_CLUSTERS.get(item.group_code, item.group_code)
        by_cluster.setdefault(cluster, []).append(item)
    cluster_scores = [
        max(_BAND_SCORE[item.band] for item in items)
        for items in by_cluster.values()
    ]
    weighted_mean = ZERO if not cluster_scores else sum(cluster_scores, ZERO) / len(cluster_scores)
    top_two = sorted(cluster_scores, reverse=True)[:2]
    top_mean = ZERO if not top_two else sum(top_two, ZERO) / len(top_two)
    breadth = Decimal(len(active)) / Decimal(max(1, len(definition.group_codes)))
    acceleration = ZERO if not active else sum(
        _clamp(item.stress_score) for item in active
    ) / Decimal(len(active))
    raw = (
        Decimal("0.45") * weighted_mean
        + Decimal("0.25") * top_mean
        + Decimal("0.20") * breadth
        + Decimal("0.10") * acceleration
    )
    evidence = tuple(
        {
            "group_code": item.group_code,
            "band": item.band.value,
            "stress_score": str(item.stress_score),
            "dependency_cluster": DEPENDENCY_CLUSTERS.get(item.group_code, item.group_code),
        }
        for item in sorted(active, key=lambda value: (-_BAND_SCORE[value.band], value.group_code))
    )
    anchor_active = not definition.anchor_groups or any(
        code in definition.anchor_groups for code in (item.group_code for item in active)
    )
    return (
        FusionComponent((_clamp(raw) * Decimal("50")).quantize(Decimal("0.01")), Decimal("50"), evidence),
        len(by_cluster),
        anchor_active,
    )


def _trend_component(
    definition: ScenarioDefinition,
    features: tuple[IndicatorFeatures, ...],
    indicator_groups: dict[str, str],
) -> FusionComponent:
    selected = [
        item
        for item in features
        if indicator_groups.get(item.indicator_code) in definition.group_codes
    ]
    if not selected:
        return FusionComponent(ZERO, Decimal("20"), ())
    score = sum((item.worsening_score for item in selected), ZERO) / Decimal(len(selected))
    regime_bonus = Decimal(sum(item.volatility_regime in {"stressed", "extreme"} for item in selected)) / Decimal(len(selected))
    score = _clamp(score * Decimal("0.8") + regime_bonus * Decimal("0.2"))
    evidence = tuple(
        {
            "indicator_code": item.indicator_code,
            "worsening_score": str(item.worsening_score),
            "regime": item.volatility_regime,
            "change_point": item.change_point,
            "state_machine": item.state_machine,
        }
        for item in sorted(selected, key=lambda value: (-value.worsening_score, value.indicator_code))[:8]
        if item.worsening_score > ZERO or item.change_point or item.state_machine not in {None, "normal"}
    )
    return FusionComponent((score * Decimal("20")).quantize(Decimal("0.01")), Decimal("20"), evidence)


def _contagion_component(contagion: ContagionFeatures | None) -> FusionComponent:
    if contagion is None:
        return FusionComponent(ZERO, Decimal("10"), ())
    correlation = contagion.mean_absolute_correlation or ZERO
    score = _clamp(contagion.breadth * Decimal("0.65") + correlation * Decimal("0.35"))
    evidence = (
        {
            "breadth": str(contagion.breadth),
            "correlation": None if contagion.mean_absolute_correlation is None else str(correlation),
            "regime": contagion.stress_correlation_regime,
            "lead_lag_edges": contagion.lead_lag_edges[:8],
        },
    )
    return FusionComponent((score * Decimal("10")).quantize(Decimal("0.01")), Decimal("10"), evidence)


def _news_component(definition: ScenarioDefinition, events: tuple[dict, ...]) -> FusionComponent:
    selected = [item for item in events if item.get("taxonomy") in definition.event_taxonomies]
    if not selected:
        return FusionComponent(ZERO, Decimal("20"), ())
    points = ZERO
    evidence = []
    for event in sorted(selected, key=lambda item: Decimal(str(item.get("event_score") or 0)), reverse=True):
        raw = _clamp(Decimal(str(event.get("event_score") or 0)))
        status = str(event.get("status") or "discovery")
        official = int(event.get("official_source_count") or 0) > 0
        corroborated = int(event.get("source_count") or 0) >= 2
        factor = Decimal("1") if official else Decimal("0.8") if corroborated else Decimal("0.25")
        points += raw * factor * Decimal("8")
        evidence.append(
            {
                "event_id": event.get("id"),
                "taxonomy": event.get("taxonomy"),
                "status": status,
                "event_score": str(raw),
                "source_count": int(event.get("source_count") or 0),
                "official_source_count": int(event.get("official_source_count") or 0),
            }
        )
    # Discovery-only material is useful for watch/search, but cannot dominate the result.
    if not any(item["official_source_count"] or item["source_count"] >= 2 for item in evidence):
        points = min(points, Decimal("5"))
    return FusionComponent(min(Decimal("20"), points).quantize(Decimal("0.01")), Decimal("20"), tuple(evidence[:8]))


def fuse_scenario(
    definition: ScenarioDefinition,
    *,
    groups: tuple[GroupState, ...],
    features: tuple[IndicatorFeatures, ...],
    indicator_groups: dict[str, str],
    contagion: ContagionFeatures | None,
    events: tuple[dict, ...],
    snapshot_at: datetime,
    coverage_status: CoverageStatus | None,
    coverage_ratio: Decimal | None,
    available_group_codes: frozenset[str] | None,
) -> ScenarioFusionState:
    numeric, independent_clusters, anchor_active = _numeric_component(definition, groups)
    trend = _trend_component(definition, features, indicator_groups)
    contagion_component = _contagion_component(contagion)
    news = _news_component(definition, events)
    strength = min(Decimal("100"), numeric.score + trend.score + contagion_component.score + news.score)
    ratio = coverage_ratio if coverage_ratio is not None else ONE
    relevant_available = (
        len(set(definition.group_codes) & available_group_codes) / len(definition.group_codes)
        if available_group_codes is not None
        else 1.0
    )
    missing_anchor = bool(
        definition.anchor_groups
        and available_group_codes is not None
        and not set(definition.anchor_groups) & available_group_codes
    )
    unknown = (
        coverage_status is CoverageStatus.INSUFFICIENT_DATA
        or relevant_available < 0.66
        or missing_anchor
    )
    numeric_confirmation = numeric.score >= Decimal("18") and independent_clusters >= 2
    if unknown:
        status = ScenarioStatus.UNKNOWN
    elif strength >= Decimal("65") and numeric_confirmation and anchor_active:
        status = ScenarioStatus.CONFIRMED
    elif strength >= Decimal("40") and independent_clusters >= 2:
        status = ScenarioStatus.ELEVATED
    elif strength >= Decimal("20") or news.score > ZERO:
        status = ScenarioStatus.WATCH
    else:
        status = ScenarioStatus.INACTIVE
    reliability = _clamp(ratio * Decimal("0.75") + min(ONE, Decimal(independent_clusters) / Decimal("3")) * Decimal("0.25"))
    confidence = (
        ScenarioConfidence.HIGH
        if reliability >= Decimal("0.85") and independent_clusters >= 3
        else ScenarioConfidence.MEDIUM
        if reliability >= Decimal("0.70") and independent_clusters >= 2
        else ScenarioConfidence.LOW
    )
    if status is ScenarioStatus.UNKNOWN:
        ru = "Недостаточно свежих обязательных данных: сценарий не оценивается как спокойный."
        en = "Required fresh data is insufficient; the scenario is not treated as inactive."
    elif status is ScenarioStatus.CONFIRMED:
        ru = f"Сценарий подтверждён {independent_clusters} независимыми числовыми каналами; события дают {news.score}/20."
        en = f"The scenario is confirmed by {independent_clusters} independent numeric channels; events add {news.score}/20."
    elif status is ScenarioStatus.ELEVATED:
        ru = f"Ухудшение широко распространяется: сила {strength}/100, независимых каналов {independent_clusters}."
        en = f"Deterioration is broadening: strength {strength}/100 across {independent_clusters} independent channels."
    elif status is ScenarioStatus.WATCH:
        ru = "Есть ранние признаки, но числового подтверждения нескольких независимых каналов пока нет."
        en = "Early signs exist, but multiple independent numeric channels do not yet confirm them."
    else:
        ru = "Свежие данные не подтверждают этот сценарий."
        en = "Fresh data do not confirm this scenario."
    canonical = json.dumps(
        {
            "version": FUSION_VERSION,
            "definition": asdict(definition),
            "snapshot_at": snapshot_at.isoformat(),
            "numeric": asdict(numeric),
            "trend": asdict(trend),
            "contagion": asdict(contagion_component),
            "news": asdict(news),
            "coverage": None if coverage_status is None else coverage_status.value,
            "coverage_ratio": None if coverage_ratio is None else str(coverage_ratio),
        },
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ScenarioFusionState(
        code=definition.code,
        snapshot_at=snapshot_at,
        status=status,
        confidence=confidence,
        strength=strength.quantize(Decimal("0.01")),
        reliability=reliability.quantize(Decimal("0.0001")),
        numeric=numeric,
        trend=trend,
        contagion=contagion_component,
        news=news,
        independent_numeric_clusters=independent_clusters,
        anchor_active=anchor_active,
        explanation_ru=ru,
        explanation_en=en,
        input_checksum=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def fuse_scenarios(
    definitions: tuple[ScenarioDefinition, ...],
    **kwargs,
) -> tuple[ScenarioFusionState, ...]:
    return tuple(fuse_scenario(definition, **kwargs) for definition in definitions)


def fusion_payload(state: ScenarioFusionState) -> dict:
    return {
        "code": state.code,
        "snapshot_at": state.snapshot_at.isoformat(),
        "status": state.status.value,
        "confidence": state.confidence.value,
        "strength": str(state.strength),
        "reliability": str(state.reliability),
        "components": {
            "numeric": asdict(state.numeric),
            "trend": asdict(state.trend),
            "contagion": asdict(state.contagion),
            "news": asdict(state.news),
        },
        "independent_numeric_clusters": state.independent_numeric_clusters,
        "anchor_active": state.anchor_active,
        "explanation": {"ru": state.explanation_ru, "en": state.explanation_en},
        "input_checksum": state.input_checksum,
        "fusion_version": FUSION_VERSION,
        "historical_probability": None,
    }
