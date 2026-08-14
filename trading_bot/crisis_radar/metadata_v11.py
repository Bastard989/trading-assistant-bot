from __future__ import annotations

from trading_bot.crisis_radar.catalog import IndicatorSeed
from trading_bot.crisis_radar.domain import RiskDirection
from trading_bot.crisis_radar.scenarios import ScenarioDefinition


GROUP_CONTEXT = {
    "labor": (
        "Рынок труда обычно ухудшается ближе к переходу замедления в рецессию.",
        "Labor conditions often deteriorate as a slowdown turns into recession.",
    ),
    "credit": (
        "Кредит показывает цену и доступность финансирования для компаний.",
        "Credit measures the price and availability of corporate financing.",
    ),
    "market_stress": (
        "Рыночный стресс быстро отражает страх, ликвидность и вынужденное снижение риска.",
        "Market stress quickly reflects fear, liquidity and forced risk reduction.",
    ),
    "crypto_leverage": (
        "Криптоплечи помогают отличить устойчивое движение от перегруженной позиции рынка.",
        "Crypto leverage helps distinguish a durable move from crowded positioning.",
    ),
    "stablecoin_stress": (
        "Расхождение исполнимых котировок USDC/USDT на нескольких биржах показывает потерю взаимной долларовой привязки или исчезновение ликвидности.",
        "Executable USDC/USDT dislocation across venues can reveal a relative peg break or disappearing liquidity.",
    ),
    "inflation_commodities": (
        "Сырьевой шок может одновременно усилить инфляцию и ослабить рост.",
        "A commodity shock can raise inflation while weakening growth.",
    ),
    "technology_market": (
        "Технологические индексы показывают, распространяется ли переоценка на сектор роста и AI.",
        "Technology indices show whether repricing is spreading through growth and AI assets.",
    ),
    "banking_stress": (
        "Депозиты и экстренное фондирование показывают, теряют ли банки устойчивую базу ресурсов.",
        "Deposits and emergency funding show whether banks are losing stable funding.",
    ),
    "dollar_liquidity": (
        "Резкое укрепление доллара способно ужесточить глобальные финансовые условия.",
        "A sharp dollar rise can tighten financial conditions around the world.",
    ),
    "housing_cre": (
        "Жильё чувствительно к ставкам и часто заранее показывает охлаждение спроса и кредита.",
        "Housing is rate-sensitive and often leads weakening demand and credit.",
    ),
    "rates_liquidity": (
        "Ставки, кривая доходности и баланс центрального банка описывают цену и доступность ликвидности.",
        "Rates, the yield curve and the central-bank balance sheet describe the price and supply of liquidity.",
    ),
}

_GROUP_NAMES = {
    "labor": ("Рынок труда США", "US labor market"),
    "credit": ("Корпоративный кредит США", "US corporate credit"),
    "market_stress": ("Рыночный стресс", "Market stress"),
    "equity_market_stress": ("Стресс фондового рынка США", "US equity-market stress"),
    "rates_liquidity": ("Ставки и ликвидность США", "US rates and liquidity"),
    "us_financial_conditions": ("Финансовые условия США", "US financial conditions"),
    "banking_stress": ("Банковский стресс США", "US banking stress"),
    "dollar_liquidity": ("Глобальная долларовая ликвидность", "Global dollar liquidity"),
    "housing_cre": ("Жильё и недвижимость США", "US housing and real estate"),
    "crypto_leverage": ("Плечи крипторынка", "Crypto leverage"),
    "crypto_price_stress": ("Ценовой стресс крипторынка", "Crypto price stress"),
    "stablecoin_stress": ("Стресс стейблкоинов", "Stablecoin stress"),
    "inflation_commodities": ("Инфляция и сырьё", "Inflation and commodities"),
    "technology_market": ("Технологический рынок США", "US technology market"),
    "real_economy": ("Реальная экономика США", "US real economy"),
    "euro_growth": ("Экономический рост еврозоны", "Euro-area growth"),
    "euro_financial_stress": ("Финансовый стресс еврозоны", "Euro-area financial stress"),
    "china_growth": ("Экономический рост Китая", "China growth"),
    "china_leading_cycle": ("Опережающий цикл Китая", "China leading cycle"),
    "global_growth": ("Мировой экономический рост", "Global economic growth"),
    "global_leading_cycle": ("Опережающий цикл G20", "G20 leading cycle"),
    "global_credit_cycle": ("Глобальный кредитный цикл", "Global credit cycle"),
    "global_supply_chain_stress": (
        "Давление в глобальных цепочках поставок",
        "Global supply-chain pressure",
    ),
}

_REGION_NAMES = {
    "canada": ("Канада", "Canada"), "uk": ("Великобритания", "United Kingdom"),
    "hong_kong": ("Гонконг", "Hong Kong"), "japan": ("Япония", "Japan"),
    "korea": ("Южная Корея", "South Korea"), "india": ("Индия", "India"),
    "brazil": ("Бразилия", "Brazil"), "mexico": ("Мексика", "Mexico"),
    "us": ("США", "United States"),
}


def group_names(code: str) -> tuple[str, str]:
    if code in _GROUP_NAMES:
        return _GROUP_NAMES[code]
    for suffix, labels in (
        ("_growth", ("Экономический рост", "economic growth")),
        ("_leading_cycle", ("Опережающий цикл", "leading cycle")),
        ("_market_conditions", ("Рыночные условия", "market conditions")),
        ("_credit_cycle", ("Кредитный цикл", "credit cycle")),
        ("_debt_service", ("Долговая нагрузка", "debt-service vulnerability")),
        ("_housing_cycle", ("Цикл цен на жильё", "housing cycle")),
        ("_labor", ("Рынок труда", "labor market")),
    ):
        if code.endswith(suffix):
            region = _REGION_NAMES.get(code[: -len(suffix)], (code[: -len(suffix)], code[: -len(suffix)]))
            return f"{labels[0]} — {region[0]}", f"{region[1]} {labels[1]}"
    return code.replace("_", " "), code.replace("_", " ")


def group_metadata(code: str) -> dict[str, str]:
    name_ru, name_en = group_names(code)
    return {
        "name_ru": name_ru,
        "name_en": name_en,
        "short_name_ru": name_ru,
        "short_name_en": name_en,
        "description_ru": "Группа объединяет зависимые показатели в один канал риска и не даёт им многократно увеличить общую ширину.",
        "description_en": "The group combines dependent indicators into one risk channel so they cannot multiply systemic breadth.",
        "why_it_matters_ru": "Несколько независимых групп важнее большого числа похожих рядов.",
        "why_it_matters_en": "Several independent groups matter more than many correlated series.",
        "worse_when_ru": "Групповой score растёт при одновременном ухудшении независимых подканалов.",
        "worse_when_en": "The group score rises when independent subchannels deteriorate together.",
        "calculation_ru": "35% центральная оценка, 30% два сильнейших независимых подканала, 20% ширина, 15% динамика; затем gates качества и покрытия.",
        "calculation_en": "35% central tendency, 30% top two independent subchannels, 20% breadth and 15% dynamics, followed by quality and coverage gates.",
        "limitations_ru": "Точные веса остаются shadow-кандидатом до replay и live canary.",
        "limitations_en": "Weights remain a shadow candidate until replay and live canary pass.",
        "source_name": "Crisis Radar dependency graph",
        "technical_code": code,
    }


def scenario_metadata(definition: ScenarioDefinition) -> dict[str, str]:
    return {
        "name_ru": definition.name_ru,
        "name_en": definition.name_en,
        "short_name_ru": definition.name_ru,
        "short_name_en": definition.name_en,
        "description_ru": "Условный сценарий, который требует совместного подтверждения независимых числовых каналов и якорей.",
        "description_en": "A conditional scenario requiring joint confirmation from independent numeric channels and anchors.",
        "why_it_matters_ru": "Сценарий переводит разрозненные сигналы в проверяемую причинную гипотезу.",
        "why_it_matters_en": "A scenario turns separate signals into a testable causal hypothesis.",
        "worse_when_ru": "Сила и ширина растут, обязательные якоря активируются, а события подтверждаются источниками.",
        "worse_when_en": "Strength and breadth rise, required anchors activate and events gain corroboration.",
        "calculation_ru": "Числовые группы агрегируются по независимым кластерам; новости добавляют evidence, но не заменяют числовое подтверждение.",
        "calculation_en": "Numeric groups aggregate by independent clusters; news adds evidence but cannot replace numeric confirmation.",
        "limitations_ru": "Вероятность остаётся пустой до прохождения исторической калибровки.",
        "limitations_en": "Probability remains null until historical calibration passes.",
        "source_name": "Crisis Radar playbook registry",
        "technical_code": definition.code,
    }


def indicator_metadata(seed: IndicatorSeed, *, source_name: str) -> dict[str, str]:
    context = GROUP_CONTEXT.get(
        seed.group_code,
        (
            "Показатель является одним из независимых элементов мировой картины риска.",
            "This indicator is one independent component of the global risk picture.",
        ),
    )
    worse = {
        RiskDirection.HIGHER_IS_WORSE: (
            "Риск растёт при повышении значения к порогам warning, danger и critical.",
            "Risk rises as the value increases toward warning, danger and critical thresholds.",
        ),
        RiskDirection.LOWER_IS_WORSE: (
            "Риск растёт при снижении значения к порогам warning, danger и critical.",
            "Risk rises as the value falls toward warning, danger and critical thresholds.",
        ),
        RiskDirection.TWO_SIDED: (
            "Риск растёт при аномальном отклонении в любую сторону; направление объясняется отдельно.",
            "Risk rises with an abnormal move in either direction; direction is explained separately.",
        ),
    }[seed.thresholds.direction]
    transform = seed.transform or "identity"
    return {
        "name_ru": seed.name_ru,
        "name_en": seed.name,
        "short_name_ru": seed.name_ru,
        "short_name_en": seed.name,
        "description_ru": (
            f"{seed.name_ru}: источник {source_name}, частота {seed.frequency}, "
            f"единица {seed.unit}."
        ),
        "description_en": (
            f"{seed.name}: source {source_name}, frequency {seed.frequency}, unit {seed.unit}."
        ),
        "why_it_matters_ru": context[0],
        "why_it_matters_en": context[1],
        "worse_when_ru": worse[0],
        "worse_when_en": worse[1],
        "calculation_ru": (
            f"Преобразование `{transform}`; экономическая полоса сравнивает значение с "
            "версионированными порогами, а v2 отдельно оценивает историю и динамику."
        ),
        "calculation_en": (
            f"Transform `{transform}`; the economic band compares the value with versioned "
            "thresholds while v2 separately scores history and dynamics."
        ),
        "limitations_ru": (
            "Один показатель не подтверждает кризис. Учитываются задержка публикации, "
            "пересмотры, свежесть и независимые каналы."
        ),
        "limitations_en": (
            "One indicator cannot confirm a crisis. Release lag, revisions, freshness and "
            "independent channels must be considered."
        ),
        "source_name": source_name,
        "technical_code": seed.code,
    }
