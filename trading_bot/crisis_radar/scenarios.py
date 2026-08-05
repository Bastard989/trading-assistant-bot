from __future__ import annotations

from dataclasses import dataclass

from trading_bot.crisis_radar.domain import (
    GroupState,
    IndicatorBand,
    ScenarioConfidence,
    ScenarioState,
    ScenarioStatus,
)


@dataclass(frozen=True)
class ScenarioDefinition:
    code: str
    name_ru: str
    name_en: str
    horizon: str
    group_codes: tuple[str, ...]
    anchor_groups: tuple[str, ...] = ()
    event_taxonomies: tuple[str, ...] = ()


SCENARIOS = (
    ScenarioDefinition(
        code="global_recession",
        name_ru="Глобальное замедление / рецессия",
        name_en="Global slowdown / recession",
        horizon="1-12m",
        group_codes=(
            "labor",
            "credit",
            "real_economy",
            "euro_growth",
            "china_growth",
            "global_growth",
            "china_leading_cycle",
            "global_leading_cycle",
            "us_financial_conditions",
        ),
        event_taxonomies=("recession_signal", "default"),
    ),
    ScenarioDefinition(
        code="financial_stress",
        name_ru="Системный финансовый стресс",
        name_en="Systemic financial stress",
        horizon="24h-3m",
        group_codes=(
            "credit",
            "market_stress",
            "euro_financial_stress",
            "equity_market_stress",
            "rates_liquidity",
            "us_financial_conditions",
            "global_credit_cycle",
        ),
        event_taxonomies=("bank_run", "emergency_liquidity", "bankruptcy", "default"),
    ),
    ScenarioDefinition(
        code="oil_stagflation",
        name_ru="Нефтяной инфляционный шок",
        name_en="Oil-driven inflation shock",
        horizon="15d-6m",
        group_codes=("inflation_commodities", "real_economy", "euro_growth"),
        anchor_groups=("inflation_commodities",),
        event_taxonomies=("commodity_shock", "supply_disruption", "armed_conflict"),
    ),
    ScenarioDefinition(
        code="crypto_leverage_unwind",
        name_ru="Криптовалютный сброс плечей",
        name_en="Crypto leverage unwind",
        horizon="24h-30d",
        group_codes=("crypto_leverage", "crypto_price_stress", "market_stress"),
        anchor_groups=("crypto_leverage",),
        event_taxonomies=("cyber_exchange_failure", "stablecoin_failure", "regulatory_restriction"),
    ),
    ScenarioDefinition(
        code="china_hard_landing",
        name_ru="Резкое замедление экономики Китая",
        name_en="China hard landing",
        horizon="3-12m",
        group_codes=(
            "china_growth",
            "china_leading_cycle",
            "global_growth",
            "global_leading_cycle",
            "global_credit_cycle",
        ),
        anchor_groups=("china_growth",),
        event_taxonomies=("recession_signal", "default", "sanctions"),
    ),
)

V2_SCENARIOS = SCENARIOS + (
    ScenarioDefinition(
        code="regional_recession",
        name_ru="Региональная рецессия",
        name_en="Regional recession",
        horizon="1-12m",
        group_codes=(
            "canada_growth", "canada_leading_cycle", "uk_growth", "uk_leading_cycle",
            "japan_growth", "japan_leading_cycle", "korea_growth", "korea_leading_cycle",
            "india_growth", "india_leading_cycle", "brazil_growth", "brazil_leading_cycle",
            "mexico_growth", "mexico_leading_cycle",
        ),
        event_taxonomies=("recession_signal", "bankruptcy", "default"),
    ),
    ScenarioDefinition(
        code="banking_crisis",
        name_ru="Банковский кризис",
        name_en="Banking crisis",
        horizon="24h-6m",
        group_codes=(
            "credit", "us_financial_conditions", "euro_financial_stress",
            "global_credit_cycle", "rates_liquidity", "market_stress",
        ),
        anchor_groups=("credit", "euro_financial_stress", "global_credit_cycle"),
        event_taxonomies=("bank_run", "emergency_liquidity", "bankruptcy"),
    ),
    ScenarioDefinition(
        code="sovereign_currency_crisis",
        name_ru="Суверенный / валютный кризис",
        name_en="Sovereign / currency crisis",
        horizon="7d-12m",
        group_codes=(
            "global_credit_cycle", "brazil_market_conditions", "mexico_market_conditions",
            "india_market_conditions", "uk_market_conditions", "japan_market_conditions",
        ),
        anchor_groups=("global_credit_cycle",),
        event_taxonomies=("default", "sanctions", "emergency_liquidity"),
    ),
    ScenarioDefinition(
        code="commodity_supply_shock",
        name_ru="Сырьевой / логистический шок",
        name_en="Commodity / supply-chain shock",
        horizon="24h-6m",
        group_codes=("inflation_commodities", "global_growth", "global_leading_cycle"),
        anchor_groups=("inflation_commodities",),
        event_taxonomies=("commodity_shock", "supply_disruption", "armed_conflict", "sanctions"),
    ),
    ScenarioDefinition(
        code="tech_ai_repricing",
        name_ru="Переоценка технологического / AI-сектора",
        name_en="Technology / AI repricing",
        horizon="1d-6m",
        group_codes=("equity_market_stress", "market_stress", "us_financial_conditions", "credit"),
        anchor_groups=("equity_market_stress",),
        event_taxonomies=("bankruptcy", "regulatory_restriction", "cyber_exchange_failure"),
    ),
    ScenarioDefinition(
        code="exchange_stablecoin_failure",
        name_ru="Крах биржи / стейблкоина",
        name_en="Exchange / stablecoin failure",
        horizon="minutes-30d",
        group_codes=("crypto_leverage", "crypto_price_stress", "market_stress"),
        anchor_groups=("crypto_leverage", "crypto_price_stress"),
        event_taxonomies=("cyber_exchange_failure", "stablecoin_failure", "regulatory_restriction"),
    ),
)


_BAND_RANK = {
    IndicatorBand.NORMAL: 0,
    IndicatorBand.WARNING: 1,
    IndicatorBand.DANGER: 2,
    IndicatorBand.CRITICAL: 3,
}


def _status_for(definition: ScenarioDefinition, selected: list[GroupState]) -> ScenarioStatus:
    bands = {item.group_code: item.band for item in selected}
    if definition.anchor_groups and not any(
        _BAND_RANK.get(bands.get(code, IndicatorBand.NORMAL), 0) >= 1
        for code in definition.anchor_groups
    ):
        return ScenarioStatus.INACTIVE
    warning = sum(_BAND_RANK[item.band] >= 1 for item in selected)
    danger = sum(_BAND_RANK[item.band] >= 2 for item in selected)
    critical = sum(_BAND_RANK[item.band] >= 3 for item in selected)
    if danger >= 2 or warning >= 3 or (critical >= 1 and warning >= 2):
        return ScenarioStatus.CONFIRMED
    if danger >= 1 or warning >= 2:
        return ScenarioStatus.ELEVATED
    if warning >= 1:
        return ScenarioStatus.WATCH
    return ScenarioStatus.INACTIVE


def _confidence(definition: ScenarioDefinition, selected: list[GroupState]) -> ScenarioConfidence:
    coverage = len(selected) / len(definition.group_codes)
    if coverage >= 1:
        return ScenarioConfidence.HIGH
    if coverage >= 0.66:
        return ScenarioConfidence.MEDIUM
    return ScenarioConfidence.LOW


def build_scenario_states(
    groups: tuple[GroupState, ...],
    *,
    available_group_codes: frozenset[str] | None = None,
    definitions: tuple[ScenarioDefinition, ...] = SCENARIOS,
) -> tuple[ScenarioState, ...]:
    by_code = {item.group_code: item for item in groups}
    result = []
    for definition in definitions:
        selected = [by_code[code] for code in definition.group_codes if code in by_code]
        data_unknown = False
        if available_group_codes is not None:
            scenario_coverage = len(set(definition.group_codes) & available_group_codes) / len(
                definition.group_codes
            )
            anchor_missing = bool(
                definition.anchor_groups
                and not set(definition.anchor_groups) & available_group_codes
            )
            data_unknown = scenario_coverage < 0.66 or anchor_missing
        status = ScenarioStatus.UNKNOWN if data_unknown else _status_for(definition, selected)
        confidence = _confidence(definition, selected)
        evidence = tuple(
            (item.group_code, item.band)
            for item in sorted(selected, key=lambda item: (-_BAND_RANK[item.band], item.group_code))
            if item.band is not IndicatorBand.NORMAL
        )
        anchor_active = not definition.anchor_groups or any(
            code in definition.anchor_groups for code, _ in evidence
        )
        if data_unknown:
            explanation_ru = "Недостаточно свежих обязательных данных для оценки этого сценария."
            explanation_en = "Required fresh data is insufficient to assess this scenario."
        elif evidence and not anchor_active:
            explanation_ru = (
                "Есть слабость связанных каналов, но обязательный якорный канал сценария не активен."
            )
            explanation_en = (
                "Related channels show weakness, but the scenario's required anchor is not active."
            )
        elif evidence:
            codes = ", ".join(code for code, _ in evidence)
            explanation_ru = f"Ухудшение подтверждают независимые каналы: {codes}."
            explanation_en = f"Deterioration is supported by independent channels: {codes}."
        else:
            explanation_ru = "Независимые каналы этого сценария пока не подтверждают ухудшение."
            explanation_en = "Independent channels do not currently confirm deterioration."
        result.append(
            ScenarioState(
                code=definition.code,
                status=status,
                confidence=confidence,
                horizon=definition.horizon,
                active_group_count=len(evidence),
                evidence=evidence,
                explanation_ru=explanation_ru,
                explanation_en=explanation_en,
            )
        )
    return tuple(result)
