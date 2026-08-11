from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from trading_bot.crisis_radar.scenarios import ScenarioDefinition
from trading_bot.crisis_radar.stage_v2 import GroupScoreV2


PLAYBOOK_VERSION = "crisis-playbook-v2-seed-1"
ZERO = Decimal("0")


@dataclass(frozen=True)
class ScenarioPlaybook:
    code: str
    description_ru: str
    description_en: str
    causal_chain_ru: tuple[str, ...]
    causal_chain_en: tuple[str, ...]
    invalidation_ru: tuple[str, ...]
    invalidation_en: tuple[str, ...]
    recovery_ru: tuple[str, ...]
    recovery_en: tuple[str, ...]
    vulnerable_assets: tuple[str, ...]
    possible_beneficiaries: tuple[str, ...]
    limitations_ru: str
    limitations_en: str


@dataclass(frozen=True)
class ScenarioStateV2:
    code: str
    status: str
    strength: Decimal
    reliability: Decimal
    active_independent_clusters: int
    current_chain_step: int
    confirmed_groups: tuple[str, ...]
    missing_anchors: tuple[str, ...]
    next_confirmations: tuple[str, ...]
    recovery_confirmations: tuple[str, ...]
    evidence_ids: tuple[int, ...]
    reasons: tuple[str, ...]
    input_checksum: str


_PLAYBOOK_CONTENT = {
    "global_recession": (
        "Широкое замедление, переходящее от опережающих данных к труду, кредиту и выпуску.",
        "A broad slowdown propagating from leading data into labor, credit and output.",
        ("Опережающие индикаторы снижаются", "Жильё и спрос охлаждаются", "Рынок труда слабеет", "Кредит дорожает", "Выпуск сокращается"),
        ("Leading indicators weaken", "Housing and demand cool", "Labor weakens", "Credit tightens", "Output contracts"),
        ("Опережающие и трудовые каналы устойчиво восстанавливаются", "Кредитные спреды не подтверждают стресс"),
        ("Leading and labor channels recover persistently", "Credit spreads do not confirm stress"),
        ("Ширина ухудшения сокращается", "Труд, кредит и выпуск восстанавливаются минимум в двух снимках"),
        ("Deterioration breadth recedes", "Labor, credit and output improve for at least two snapshots"),
        ("EQUITIES", "HIGH_YIELD", "CYCLICALS", "CRYPTO"),
        ("GOVERNMENT_BONDS", "DEFENSIVES", "GOLD"),
    ),
    "financial_stress": (
        "Нарушение рыночного фондирования, которое распространяется на кредит, банки и ликвидность.",
        "A market-funding disruption spreading into credit, banks and liquidity.",
        ("Волатильность растёт", "Ликвидность ухудшается", "Спреды расширяются", "Банки теряют фондирование", "Появляются экстренные меры"),
        ("Volatility rises", "Liquidity deteriorates", "Spreads widen", "Banks lose funding", "Emergency support appears"),
        ("Спреды и фондирование нормализуются", "Рыночный стресс не распространяется"),
        ("Spreads and funding normalize", "Market stress does not spread"),
        ("Экстренное фондирование снижается", "Спреды и ширина рынка восстанавливаются"),
        ("Emergency funding declines", "Spreads and market breadth recover"),
        ("BANKS", "HIGH_YIELD", "EQUITIES", "LEVERAGED_CRYPTO"),
        ("CASH", "SHORT_GOVERNMENT_BONDS", "GOLD"),
    ),
    "banking_crisis": (
        "Отток устойчивого банковского фондирования с переходом к экстренной ликвидности и кредитному сжатию.",
        "Loss of stable bank funding followed by emergency liquidity use and credit contraction.",
        ("Депозиты сокращаются", "Экстренные заимствования растут", "Банковский риск подтверждается рынком", "Кредитование сжимается"),
        ("Deposits decline", "Emergency borrowing rises", "Markets confirm bank risk", "Credit contracts"),
        ("Депозиты стабилизируются без экстренного фондирования",),
        ("Deposits stabilize without emergency funding",),
        ("Отток прекращается", "Экстренные линии сворачиваются", "Кредитные условия улучшаются"),
        ("Outflows stop", "Emergency facilities unwind", "Credit conditions improve"),
        ("BANKS", "FINANCIALS", "HIGH_YIELD"),
        ("SOVEREIGN_BONDS", "QUALITY_EQUITIES", "GOLD"),
    ),
    "oil_stagflation": (
        "Нефтяной шок одновременно усиливает инфляцию и ослабляет реальный спрос.",
        "An oil shock raises inflation while weakening real demand.",
        ("Нефть резко дорожает", "Инфляционные ожидания растут", "Реальные доходы и рост снижаются"),
        ("Oil rises sharply", "Inflation expectations rise", "Real income and growth weaken"),
        ("Поставки восстанавливаются и цены возвращаются без ухудшения роста",),
        ("Supply normalizes and prices reverse without weaker growth",),
        ("Цены и логистика нормализуются", "Рост перестаёт ухудшаться"),
        ("Prices and logistics normalize", "Growth stops weakening"),
        ("CONSUMER_DISCRETIONARY", "LONG_DURATION_BONDS"),
        ("ENERGY", "COMMODITIES", "INFLATION_LINKED_BONDS"),
    ),
    "commodity_supply_shock": (
        "Нарушение поставок сырья или логистики с передачей в цены и выпуск.",
        "A commodity or logistics disruption transmitting into prices and output.",
        ("Возникает перебой", "Сырьё дорожает", "Сроки поставок растут", "Выпуск и маржа снижаются"),
        ("A disruption occurs", "Commodities rise", "Delivery times extend", "Output and margins weaken"),
        ("Потоки и запасы быстро восстанавливаются",),
        ("Flows and inventories recover quickly",),
        ("Перебои затухают", "Ценовой импульс разворачивается"),
        ("Disruptions fade", "Price momentum reverses"),
        ("TRANSPORT", "IMPORTERS", "CYCLICALS"),
        ("PRODUCERS", "COMMODITIES", "LOGISTICS_ALTERNATIVES"),
    ),
    "crypto_leverage_unwind": (
        "Скопление плечей сменяется ликвидационным снижением OI и цены.",
        "Crowded leverage turns into a liquidation-driven fall in OI and price.",
        ("OI и funding растут", "Цена перестаёт подтверждать", "Цена и OI резко падают", "Волатильность распространяется"),
        ("OI and funding build", "Price stops confirming", "Price and OI fall sharply", "Volatility spreads"),
        ("Плечо снижается без ценового стресса",),
        ("Leverage declines without price stress",),
        ("OI стабилизируется", "Funding нормализуется", "Цена формирует восстановление"),
        ("OI stabilizes", "Funding normalizes", "Price establishes recovery"),
        ("BTC", "ETH", "ALTCOINS", "CRYPTO_EQUITIES"),
        ("CASH", "UNLEVERAGED_SPOT"),
    ),
    "exchange_stablecoin_failure": (
        "Операционный сбой, неплатёжеспособность или потеря привязки в криптоинфраструктуре.",
        "An operational failure, insolvency or depeg in crypto infrastructure.",
        ("Появляется официальное событие", "Вывод средств/ликвидность нарушаются", "Депег или ценовой стресс распространяется"),
        ("An official event appears", "Withdrawals or liquidity fail", "Depeg or price stress spreads"),
        ("Резервы и погашения подтверждены проверяемыми данными",),
        ("Reserves and redemptions are verified by auditable data",),
        ("Вывод средств работает", "Привязка восстановлена", "Заражение прекращается"),
        ("Withdrawals work", "Peg is restored", "Contagion stops"),
        ("STABLECOINS", "EXCHANGE_TOKENS", "ALTCOINS"),
        ("CASH", "SELF_CUSTODY", "MAJOR_SPOT_ASSETS"),
    ),
    "china_hard_landing": (
        "Резкое замедление Китая с передачей в кредит, валюту, сырьё и мировой спрос.",
        "A sharp China slowdown transmitting through credit, FX, commodities and global demand.",
        ("CLI ухудшается", "Кредитный цикл слабеет", "Юань испытывает стресс", "Рост и импорт снижаются"),
        ("CLI weakens", "Credit cycle deteriorates", "The yuan comes under stress", "Growth and imports slow"),
        ("Опережающий цикл и кредит устойчиво разворачиваются вверх",),
        ("Leading cycle and credit turn upward persistently",),
        ("Валюта и кредит стабилизируются", "Глобальный спрос перестаёт ухудшаться"),
        ("FX and credit stabilize", "Global demand stops worsening"),
        ("CHINA_EQUITIES", "INDUSTRIAL_METALS", "ASIA_FX"),
        ("DEFENSIVES", "POLICY_BENEFICIARIES"),
    ),
    "sovereign_currency_crisis": (
        "Сочетание валютного, долгового и ликвидностного давления на государство или регион.",
        "Combined FX, debt and liquidity pressure on a sovereign or region.",
        ("Валюта падает", "Долларовая ликвидность ухудшается", "Кредитный риск растёт", "Возникают ограничения или реструктуризация"),
        ("FX falls", "Dollar liquidity tightens", "Credit risk rises", "Controls or restructuring appear"),
        ("Резервы, валюта и доступ к фондированию стабилизируются",),
        ("Reserves, FX and market access stabilize",),
        ("Валюта и спреды восстанавливаются", "Ограничения отменяются"),
        ("FX and spreads recover", "Restrictions are lifted"),
        ("LOCAL_FX", "SOVEREIGN_DEBT", "LOCAL_BANKS"),
        ("USD", "GOLD", "EXTERNAL_QUALITY_ASSETS"),
    ),
    "regional_recession": (
        "Замедление ограниченного числа стран без достаточной ширины для глобальной рецессии.",
        "A slowdown confined to selected countries without global recession breadth.",
        ("Региональные CLI снижаются", "Рост и жильё ослабевают", "Слабость распространяется на труд и кредит"),
        ("Regional CLIs fall", "Growth and housing weaken", "Weakness spreads to labor and credit"),
        ("Ухудшение остаётся изолированным и опережающие данные разворачиваются",),
        ("Weakness stays isolated and leading data turn",),
        ("Региональная ширина сокращается", "Рост стабилизируется"),
        ("Regional breadth recedes", "Growth stabilizes"),
        ("REGIONAL_EQUITIES", "LOCAL_FX", "CYCLICALS"),
        ("REGIONAL_BONDS", "EXPORT_BENEFICIARIES"),
    ),
    "tech_ai_repricing": (
        "Переоценка дорогих технологических активов из-за ставок, прибыли или концентрации.",
        "A repricing of expensive technology assets driven by rates, earnings or concentration.",
        ("Реальные ставки растут", "Лидерство рынка сужается", "Технологии теряют относительную силу", "Кредит подтверждает стресс"),
        ("Real yields rise", "Market leadership narrows", "Technology loses relative strength", "Credit confirms stress"),
        ("Прибыль и ширина рынка подтверждают оценки",),
        ("Earnings and breadth validate valuations",),
        ("Реальные ставки и спреды снижаются", "Ширина технологического рынка восстанавливается"),
        ("Real yields and spreads decline", "Technology breadth recovers"),
        ("HIGH_DURATION_TECH", "SEMICONDUCTORS", "VENTURE_SENSITIVE_ASSETS"),
        ("VALUE", "QUALITY_CASH_FLOW", "SHORT_DURATION_ASSETS"),
    ),
}


def playbook_for(code: str) -> ScenarioPlaybook:
    content = _PLAYBOOK_CONTENT[code]
    return ScenarioPlaybook(
        code=code,
        description_ru=content[0], description_en=content[1],
        causal_chain_ru=content[2], causal_chain_en=content[3],
        invalidation_ru=content[4], invalidation_en=content[5],
        recovery_ru=content[6], recovery_en=content[7],
        vulnerable_assets=content[8], possible_beneficiaries=content[9],
        limitations_ru="Это условный сценарий наблюдения, а не приказ на сделку и не обещание прибыли.",
        limitations_en="This is a conditional monitoring scenario, not a trade order or profit promise.",
    )


def calculate_scenario_v2(
    definition: ScenarioDefinition,
    groups: tuple[GroupScoreV2, ...],
    *,
    evidence_ids: tuple[int, ...] = (),
    numeric_coverage: Decimal = Decimal("1"),
    news_coverage: Decimal = Decimal("1"),
    previous_status: str | None = None,
    previous_strength: Decimal | None = None,
) -> ScenarioStateV2:
    selected = [group for group in groups if group.group_code in definition.group_codes]
    active = [group for group in selected if group.score >= Decimal(".25")]
    cluster_scores: dict[str, Decimal] = {}
    for group in active:
        cluster_scores[group.cluster_code] = max(
            cluster_scores.get(group.cluster_code, ZERO), group.score
        )
    values = list(cluster_scores.values())
    mean_score = ZERO if not values else sum(values, ZERO) / Decimal(len(values))
    top = sorted(values, reverse=True)[:2]
    top_score = ZERO if not top else sum(top, ZERO) / Decimal(len(top))
    breadth = Decimal(len(active)) / Decimal(max(1, len(definition.group_codes)))
    strength = (
        (mean_score * Decimal(".60") + top_score * Decimal(".25") + breadth * Decimal(".15"))
        * Decimal("100")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    present_groups = {group.group_code for group in active}
    missing_anchors = tuple(code for code in definition.anchor_groups if code not in present_groups)
    anchors_ok = not definition.anchor_groups or len(missing_anchors) < len(definition.anchor_groups)
    reliability = (
        min(Decimal("1"), numeric_coverage) * Decimal(".80")
        + min(Decimal("1"), news_coverage) * Decimal(".10")
        + min(Decimal("1"), Decimal(len(values)) / Decimal("3")) * Decimal(".10")
    ).quantize(Decimal("0.0001"))
    if numeric_coverage < Decimal(".70"):
        status = "unknown"
    elif strength >= Decimal("60") and len(values) >= 2 and anchors_ok:
        status = "confirmed"
    elif strength >= Decimal("40") and len(values) >= 2:
        status = "elevated"
    elif strength >= Decimal("20") or evidence_ids:
        status = "watch"
    else:
        status = "inactive"
    recovery_confirmations = tuple(
        group.group_code for group in selected if group.score < Decimal(".25")
    )
    if (
        previous_status in {"elevated", "confirmed", "recovery_watch", "recovery_confirmed"}
        and previous_strength is not None
        and previous_strength - strength >= Decimal("15")
    ):
        status = "recovery_confirmed" if len(recovery_confirmations) >= 2 else "recovery_watch"
    ordered = sorted(active, key=lambda group: (-group.score, group.group_code))
    current_step = min(len(playbook_for(definition.code).causal_chain_ru), len(ordered))
    next_confirmations = tuple(
        code for code in definition.group_codes if code not in present_groups
    )[:4]
    reasons = tuple(
        [f"active_cluster:{code}" for code in sorted(cluster_scores)]
        + [f"missing_anchor:{code}" for code in missing_anchors]
        + (["news_coverage_degraded"] if news_coverage < Decimal(".70") else [])
    )
    canonical = json.dumps(
        {
            "version": PLAYBOOK_VERSION,
            "definition": definition.code,
            "groups": {group.group_code: str(group.score) for group in selected},
            "evidence_ids": evidence_ids,
            "numeric_coverage": str(numeric_coverage),
            "news_coverage": str(news_coverage),
            "previous_status": previous_status,
            "previous_strength": None if previous_strength is None else str(previous_strength),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return ScenarioStateV2(
        code=definition.code, status=status, strength=strength, reliability=reliability,
        active_independent_clusters=len(values), current_chain_step=current_step,
        confirmed_groups=tuple(group.group_code for group in ordered),
        missing_anchors=missing_anchors, next_confirmations=next_confirmations,
        recovery_confirmations=recovery_confirmations[:4], evidence_ids=evidence_ids,
        reasons=reasons, input_checksum=hashlib.sha256(canonical.encode()).hexdigest(),
    )
