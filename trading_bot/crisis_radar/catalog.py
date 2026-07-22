from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from trading_bot.crisis_radar.domain import IndicatorThresholds, RiskDirection
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.scenarios import SCENARIOS
from trading_bot.crisis_radar.stability import STABILITY_POLICY


METHODOLOGY_CODE = "crisis-radar"
METHODOLOGY_VERSION = "starter-v8"


@dataclass(frozen=True)
class SourceSeed:
    code: str
    name: str
    base_url: str
    terms_url: str
    expected_frequency: str
    max_staleness_seconds: int


@dataclass(frozen=True)
class IndicatorSeed:
    code: str
    provider_series_id: str
    name: str
    name_ru: str
    group_code: str
    region_code: str
    unit: str
    frequency: str
    max_staleness_seconds: int
    thresholds: IndicatorThresholds
    transform: str = "identity"


@dataclass(frozen=True)
class ResearchIndicatorSeed:
    """Stored source series used by replay/labels but excluded from live risk scoring."""

    code: str
    provider_series_id: str
    name: str
    group_code: str
    region_code: str
    unit: str
    frequency: str
    max_staleness_seconds: int
    transform: str = "identity"


@dataclass(frozen=True)
class NewsSourceSeed:
    code: str
    name: str
    base_url: str
    terms_url: str


FRED = SourceSeed(
    code="fred",
    name="Federal Reserve Economic Data",
    base_url="https://api.stlouisfed.org/fred",
    terms_url="https://fred.stlouisfed.org/docs/api/terms_of_use.html",
    expected_frequency="mixed",
    max_staleness_seconds=45 * 86400,
)

BEA = SourceSeed(
    code="bea",
    name="US Bureau of Economic Analysis",
    base_url="https://apps.bea.gov/api/data",
    terms_url="https://apps.bea.gov/API/bea_web_service_api_user_guide.htm",
    expected_frequency="quarterly",
    max_staleness_seconds=120 * 86400,
)

EIA = SourceSeed(
    code="eia",
    name="US Energy Information Administration",
    base_url="https://api.eia.gov/v2",
    terms_url="https://www.eia.gov/opendata/terms.php",
    expected_frequency="daily",
    max_staleness_seconds=10 * 86400,
)

ECB = SourceSeed(
    code="ecb",
    name="European Central Bank Data Portal",
    base_url="https://data-api.ecb.europa.eu/service/data",
    terms_url="https://data.ecb.europa.eu/help/api/overview",
    expected_frequency="daily",
    max_staleness_seconds=10 * 86400,
)

EUROSTAT = SourceSeed(
    code="eurostat",
    name="Eurostat",
    base_url="https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data",
    terms_url="https://ec.europa.eu/eurostat/about-us/policies/copyright",
    expected_frequency="quarterly",
    max_staleness_seconds=120 * 86400,
)

BYBIT = SourceSeed(
    code="bybit",
    name="Bybit Public Market Data",
    base_url="https://api.bybit.com/v5/market",
    terms_url="https://www.bybit.com/en/help-center/article/Bybit-Website-Terms-and-Conditions",
    expected_frequency="mixed",
    max_staleness_seconds=2 * 86400,
)

WORLD_BANK = SourceSeed(
    code="world_bank",
    name="World Bank Indicators API",
    base_url="https://api.worldbank.org/v2",
    terms_url="https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets",
    expected_frequency="annual",
    max_staleness_seconds=800 * 86400,
)

BIS = SourceSeed(
    code="bis",
    name="Bank for International Settlements Data Portal",
    base_url="https://data.bis.org",
    terms_url="https://www.bis.org/terms_conditions.htm",
    expected_frequency="quarterly",
    max_staleness_seconds=220 * 86400,
)

OECD = SourceSeed(
    code="oecd",
    name="OECD SDMX Data Explorer",
    base_url="https://sdmx.oecd.org/public/rest/v1/data",
    terms_url="https://www.oecd.org/en/about/terms-conditions.html",
    expected_frequency="monthly",
    max_staleness_seconds=75 * 86400,
)

NEWS_SOURCES = (
    NewsSourceSeed(
        code="fed_news",
        name="Federal Reserve Monetary Policy RSS",
        base_url="https://www.federalreserve.gov/feeds/press_monetary.xml",
        terms_url="https://www.federalreserve.gov/feeds/feeds.htm",
    ),
    NewsSourceSeed(
        code="ecb_news",
        name="European Central Bank Press RSS",
        base_url="https://www.ecb.europa.eu/rss/press.html",
        terms_url="https://www.ecb.europa.eu/home/html/rss.en.html",
    ),
)

FRED_INDICATORS = (
    IndicatorSeed(
        code="sahm_rule",
        provider_series_id="SAHMREALTIME",
        name="Sahm Rule Recession Indicator",
        name_ru="Индикатор рецессии Sahm Rule",
        group_code="labor",
        region_code="US",
        unit="percentage_points",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("0.25"),
            danger=Decimal("0.50"),
            critical=Decimal("1.00"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_hy_oas",
        provider_series_id="BAMLH0A0HYM2",
        name="US High Yield Option-Adjusted Spread",
        name_ru="Спред высокодоходных облигаций США",
        group_code="credit",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("4.5"),
            danger=Decimal("6"),
            critical=Decimal("8"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="vix",
        provider_series_id="VIXCLS",
        name="CBOE Volatility Index",
        name_ru="Индекс волатильности VIX",
        group_code="market_stress",
        region_code="US",
        unit="index_points",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("25"),
            danger=Decimal("30"),
            critical=Decimal("40"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="sp500_30d_drawdown",
        provider_series_id="SP500",
        name="S&P 500 30-Day Drawdown",
        name_ru="Просадка S&P 500 от 30-дневного максимума",
        group_code="equity_market_stress",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-10"),
            danger=Decimal("-20"),
            critical=Decimal("-35"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="drawdown_30d",
    ),
    IndicatorSeed(
        code="us_10y2y_spread",
        provider_series_id="T10Y2Y",
        name="US 10-Year Minus 2-Year Treasury Spread",
        name_ru="Спред доходностей Treasury 10Y–2Y",
        group_code="rates_liquidity",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("0"),
            danger=Decimal("-0.5"),
            critical=Decimal("-1"),
            reference=Decimal("1.5"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_nfci",
        provider_series_id="NFCI",
        name="Chicago Fed National Financial Conditions Index",
        name_ru="Национальный индекс финансовых условий ФРБ Чикаго",
        group_code="us_financial_conditions",
        region_code="US",
        unit="index",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("0"),
            danger=Decimal("0.5"),
            critical=Decimal("1.5"),
            reference=Decimal("-0.5"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="fed_assets_90d_change",
        provider_series_id="WALCL",
        name="Federal Reserve Total Assets 90-Day Change",
        name_ru="Изменение совокупных активов ФРС за 90 дней",
        group_code="rates_liquidity",
        region_code="US",
        unit="percent",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-2"),
            danger=Decimal("-5"),
            critical=Decimal("-10"),
            reference=Decimal("2"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="change_90d",
    ),
)

BEA_INDICATORS = (
    IndicatorSeed(
        code="us_real_gdp_qoq",
        provider_series_id="NIPA:T10101:1",
        name="US Real GDP Growth (Quarterly Annualized)",
        name_ru="Рост реального ВВП США, квартальный annualized",
        group_code="real_economy",
        region_code="US",
        unit="percent_annualized",
        frequency="quarterly",
        max_staleness_seconds=120 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("1"),
            danger=Decimal("0"),
            critical=Decimal("-2"),
            reference=Decimal("3"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
    ),
)

EIA_INDICATORS = (
    IndicatorSeed(
        code="wti_90d_change",
        provider_series_id="RWTC:90d_change",
        name="WTI Spot Price 90-Day Change",
        name_ru="Изменение спотовой цены WTI за 90 дней",
        group_code="inflation_commodities",
        region_code="GLOBAL",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=10 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("20"),
            danger=Decimal("35"),
            critical=Decimal("60"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
)

ECB_INDICATORS = (
    IndicatorSeed(
        code="euro_ciss",
        provider_series_id="CISS.D.U2.Z0Z.4F.EC.SS_CIN.IDX",
        name="Euro Area New Composite Indicator of Systemic Stress",
        name_ru="Новый композитный индикатор системного стресса еврозоны",
        group_code="euro_financial_stress",
        region_code="EA20",
        unit="index",
        frequency="daily",
        max_staleness_seconds=10 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("0.20"),
            danger=Decimal("0.35"),
            critical=Decimal("0.55"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
)

EUROSTAT_INDICATORS = (
    IndicatorSeed(
        code="euro_real_gdp_qoq",
        provider_series_id="namq_10_gdp:EA20:CLV_PCH_PRE",
        name="Euro Area Real GDP Growth (Quarter over Quarter)",
        name_ru="Рост реального ВВП еврозоны к предыдущему кварталу",
        group_code="euro_growth",
        region_code="EA20",
        unit="percent",
        frequency="quarterly",
        max_staleness_seconds=120 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("0.2"),
            danger=Decimal("-0.1"),
            critical=Decimal("-1.0"),
            reference=Decimal("0.5"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
    ),
)

WORLD_BANK_INDICATORS = (
    IndicatorSeed(
        code="china_real_gdp_yoy",
        provider_series_id="CHN:NY.GDP.MKTP.KD.ZG",
        name="China Real GDP Growth (Annual)",
        name_ru="Рост реального ВВП Китая за год",
        group_code="china_growth",
        region_code="CHN",
        unit="percent",
        frequency="annual",
        max_staleness_seconds=800 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("4"),
            danger=Decimal("2"),
            critical=Decimal("0"),
            reference=Decimal("6"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="world_real_gdp_yoy",
        provider_series_id="WLD:NY.GDP.MKTP.KD.ZG",
        name="World Real GDP Growth (Annual)",
        name_ru="Рост мирового реального ВВП за год",
        group_code="global_growth",
        region_code="GLOBAL",
        unit="percent",
        frequency="annual",
        max_staleness_seconds=800 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("2.5"),
            danger=Decimal("1.5"),
            critical=Decimal("0"),
            reference=Decimal("3.5"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
    ),
)

BIS_INDICATORS = tuple(
    IndicatorSeed(
        code=f"{country}_credit_to_gdp_gap",
        provider_series_id=f"WS_CREDIT_GAP:{iso}:P:A:C",
        name=f"{label} Private-Sector Credit-to-GDP Gap",
        name_ru=f"Кредитный разрыв частного сектора — {label_ru}",
        group_code="global_credit_cycle",
        region_code=iso,
        unit="percentage_points",
        frequency="quarterly",
        max_staleness_seconds=220 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("2"),
            danger=Decimal("10"),
            critical=Decimal("20"),
            reference=Decimal("-5"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    )
    for country, iso, label, label_ru in (
        ("us", "US", "United States", "США"),
        ("china", "CN", "China", "Китай"),
    )
)

OECD_INDICATORS = (
    IndicatorSeed(
        code="g20_cli_6m_change",
        provider_series_id="DSD_STES@DF_CLI:G20:M:LI:AA:H:6m_change",
        name="G20 Composite Leading Indicator 6-Month Change",
        name_ru="Изменение опережающего индикатора G20 за 6 месяцев",
        group_code="global_leading_cycle",
        region_code="G20",
        unit="index_points_6m",
        frequency="monthly",
        max_staleness_seconds=75 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-0.2"),
            danger=Decimal("-0.6"),
            critical=Decimal("-1.2"),
            reference=Decimal("0.3"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="change_6m",
    ),
    IndicatorSeed(
        code="china_cli_6m_change",
        provider_series_id="DSD_STES@DF_CLI:CHN:M:LI:AA:H:6m_change",
        name="China Composite Leading Indicator 6-Month Change",
        name_ru="Изменение опережающего индикатора Китая за 6 месяцев",
        group_code="china_leading_cycle",
        region_code="CHN",
        unit="index_points_6m",
        frequency="monthly",
        max_staleness_seconds=75 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-0.2"),
            danger=Decimal("-0.6"),
            critical=Decimal("-1.2"),
            reference=Decimal("0.3"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="change_6m",
    ),
)

BYBIT_INDICATORS = tuple(
    indicator
    for symbol, label in (("btc", "Bitcoin"), ("eth", "Ethereum"))
    for indicator in (
        IndicatorSeed(
            code=f"{symbol}_funding_rate",
            provider_series_id=f"{symbol.upper()}USDT:funding",
            name=f"{label} Perpetual Funding Rate",
            name_ru=f"Funding бессрочного контракта {symbol.upper()}",
            group_code="crypto_leverage",
            region_code="CRYPTO",
            unit="percent",
            frequency="funding_interval",
            max_staleness_seconds=2 * 86400,
            thresholds=IndicatorThresholds(
                warning=Decimal("0.05"),
                danger=Decimal("0.10"),
                critical=Decimal("0.20"),
                direction=RiskDirection.TWO_SIDED,
            ),
        ),
        IndicatorSeed(
            code=f"{symbol}_oi_7d_abs_change",
            provider_series_id=f"{symbol.upper()}USDT:oi:7d_abs_change",
            name=f"{label} Open Interest 7-Day Absolute Change",
            name_ru=f"Абсолютное изменение OI {symbol.upper()} за 7 дней",
            group_code="crypto_leverage",
            region_code="CRYPTO",
            unit="percent",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
            thresholds=IndicatorThresholds(
                warning=Decimal("20"),
                danger=Decimal("35"),
                critical=Decimal("60"),
                direction=RiskDirection.HIGHER_IS_WORSE,
            ),
        ),
        IndicatorSeed(
            code=f"{symbol}_30d_drawdown",
            provider_series_id=f"{symbol.upper()}USDT:30d_drawdown",
            name=f"{label} 30-Day Drawdown",
            name_ru=f"Просадка {symbol.upper()} от 30-дневного максимума",
            group_code="crypto_price_stress",
            region_code="CRYPTO",
            unit="percent",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
            thresholds=IndicatorThresholds(
                warning=Decimal("-15"),
                danger=Decimal("-25"),
                critical=Decimal("-40"),
                direction=RiskDirection.LOWER_IS_WORSE,
            ),
        ),
    )
)


BYBIT_RESEARCH_INDICATORS = tuple(
    indicator
    for symbol, label in (("btc", "Bitcoin"), ("eth", "Ethereum"))
    for indicator in (
        ResearchIndicatorSeed(
            code=f"{symbol}_close_price",
            provider_series_id=f"{symbol.upper()}USDT:close:1d",
            name=f"{label} Completed Daily Close",
            group_code="crypto_research",
            region_code="CRYPTO",
            unit="USDT",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
        ),
        ResearchIndicatorSeed(
            code=f"{symbol}_return_7d",
            provider_series_id=f"{symbol.upper()}USDT:return:7d",
            name=f"{label} 7-Day Return",
            group_code="crypto_research",
            region_code="CRYPTO",
            unit="percent",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
        ),
        ResearchIndicatorSeed(
            code=f"{symbol}_open_interest",
            provider_series_id=f"{symbol.upper()}USDT:oi:1d",
            name=f"{label} Open Interest",
            group_code="crypto_research",
            region_code="CRYPTO",
            unit="coin",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
        ),
        ResearchIndicatorSeed(
            code=f"{symbol}_oi_7d_change",
            provider_series_id=f"{symbol.upper()}USDT:oi:7d_change",
            name=f"{label} Open Interest 7-Day Signed Change",
            group_code="crypto_research",
            region_code="CRYPTO",
            unit="percent",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
        ),
    )
)

STARTER_INDICATORS = (
    FRED_INDICATORS
    + BEA_INDICATORS
    + EIA_INDICATORS
    + ECB_INDICATORS
    + EUROSTAT_INDICATORS
    + WORLD_BANK_INDICATORS
    + BIS_INDICATORS
    + OECD_INDICATORS
    + BYBIT_INDICATORS
)


def methodology_checksum() -> str:
    payload = {
        "methodology": [METHODOLOGY_CODE, METHODOLOGY_VERSION],
        "sources": [
            asdict(item)
            for item in (FRED, BEA, EIA, ECB, EUROSTAT, WORLD_BANK, BIS, OECD, BYBIT)
        ],
        "indicators": [
            {
                **asdict(item),
                "thresholds": {
                    "warning": str(item.thresholds.warning),
                    "danger": str(item.thresholds.danger),
                    "critical": str(item.thresholds.critical),
                    "direction": item.thresholds.direction.value,
                    "reference": str(item.thresholds.reference),
                },
            }
            for item in STARTER_INDICATORS
        ],
        "scenarios": [asdict(item) for item in SCENARIOS],
        "stability": {
            **asdict(STABILITY_POLICY),
            "recovery_fraction": str(STABILITY_POLICY.recovery_fraction),
        },
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def bootstrap_starter_catalog(repository: CrisisRadarRepository) -> dict[str, int | str]:
    methodology_id = repository.register_methodology(
        METHODOLOGY_CODE,
        METHODOLOGY_VERSION,
        checksum=methodology_checksum(),
        effective_from="2026-07-21T00:00:00+00:00",
    )
    for source in (FRED, BEA, EIA, ECB, EUROSTAT, WORLD_BANK, BIS, OECD, BYBIT):
        repository.register_source(
            source.code,
            source.name,
            base_url=source.base_url,
            terms_url=source.terms_url,
            expected_frequency=source.expected_frequency,
            max_staleness_seconds=source.max_staleness_seconds,
        )
    for source in NEWS_SOURCES:
        repository.register_source(
            source.code,
            source.name,
            base_url=source.base_url,
            terms_url=source.terms_url,
            access_type="rss",
            expected_frequency="intraday",
            max_staleness_seconds=2 * 86400,
        )
    for item in STARTER_INDICATORS:
        source_code = next(
            source.code
            for source, indicators in (
                (FRED, FRED_INDICATORS),
                (BEA, BEA_INDICATORS),
                (EIA, EIA_INDICATORS),
                (ECB, ECB_INDICATORS),
                (EUROSTAT, EUROSTAT_INDICATORS),
                (WORLD_BANK, WORLD_BANK_INDICATORS),
                (BIS, BIS_INDICATORS),
                (OECD, OECD_INDICATORS),
                (BYBIT, BYBIT_INDICATORS),
            )
            if item in indicators
        )
        indicator_id = repository.register_indicator(
            item.code,
            item.name,
            group_code=item.group_code,
            unit=item.unit,
            frequency=item.frequency,
            risk_direction=item.thresholds.direction.value,
            source_code=source_code,
            region_code=item.region_code,
            provider_series_id=item.provider_series_id,
            transform=item.transform,
            max_staleness_seconds=item.max_staleness_seconds,
        )
        repository.register_thresholds(indicator_id, methodology_id, item.thresholds)
    for item in BYBIT_RESEARCH_INDICATORS:
        repository.register_indicator(
            item.code,
            item.name,
            group_code=item.group_code,
            unit=item.unit,
            frequency=item.frequency,
            risk_direction=RiskDirection.TWO_SIDED.value,
            source_code=BYBIT.code,
            region_code=item.region_code,
            provider_series_id=item.provider_series_id,
            transform=item.transform,
            max_staleness_seconds=item.max_staleness_seconds,
            enabled=False,
        )
    for scenario in SCENARIOS:
        repository.register_scenario(
            scenario.code,
            methodology_id,
            name_ru=scenario.name_ru,
            name_en=scenario.name_en,
            horizon=scenario.horizon,
            group_codes=scenario.group_codes,
            anchor_groups=scenario.anchor_groups,
        )
    return {
        "methodology_id": methodology_id,
        "methodology_version": METHODOLOGY_VERSION,
        "indicator_count": len(STARTER_INDICATORS),
        "research_indicator_count": len(BYBIT_RESEARCH_INDICATORS),
        "scenario_count": len(SCENARIOS),
    }
