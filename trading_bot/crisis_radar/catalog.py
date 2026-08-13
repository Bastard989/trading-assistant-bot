from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from decimal import Decimal

from trading_bot.crisis_radar.domain import IndicatorThresholds, RiskDirection
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.scoring_v2 import PROFILES, profile_for
from trading_bot.crisis_radar.scenarios import SCENARIOS, V2_SCENARIOS, ScenarioDefinition
from trading_bot.crisis_radar.stage_v2 import (
    DEPENDENCY_GRAPH_VERSION,
    STAGE_VERSION,
    dependency_for,
)
from trading_bot.crisis_radar.stability import STABILITY_POLICY


METHODOLOGY_CODE = "crisis-radar"
METHODOLOGY_VERSION = "starter-v8"
METHODOLOGY_V2_VERSION = "candidate-v9"
METHODOLOGY_GLOBAL_V2_VERSION = "candidate-v10"
METHODOLOGY_V11_VERSION = "candidate-v11"
METHODOLOGY_V12_VERSION = "candidate-v12"
METHODOLOGY_V13_VERSION = "candidate-v13"


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
    historical_backfill_mode: str = "initial_release"


@dataclass(frozen=True)
class NewsSourceSeed:
    code: str
    name: str
    base_url: str
    terms_url: str
    access_type: str = "rss"


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
    NewsSourceSeed(
        code="sec_news",
        name="US Securities and Exchange Commission Press Releases",
        base_url="https://www.sec.gov/news/pressreleases.rss",
        terms_url="https://www.sec.gov/about/rss-feeds",
    ),
    NewsSourceSeed(
        code="cftc_news",
        name="US Commodity Futures Trading Commission Press Releases",
        base_url="https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
        terms_url="https://www.cftc.gov/RSS/index.htm",
    ),
    NewsSourceSeed(
        code="bis_news",
        name="Bank for International Settlements Press Releases",
        base_url="https://www.bis.org/doclist/all_pressrels.rss",
        terms_url="https://www.bis.org/rss/index.htm",
    ),
    NewsSourceSeed(
        code="boj_news",
        name="Bank of Japan What's New RSS",
        base_url="https://www.boj.or.jp/en/rss/whatsnew.xml",
        terms_url="https://www.boj.or.jp/en/tips.htm",
    ),
    NewsSourceSeed(
        code="rbi_news",
        name="Reserve Bank of India Press Releases RSS",
        base_url="https://rbi.org.in/pressreleases_rss.xml",
        terms_url="https://www.rbi.org.in/Scripts/rss.aspx",
    ),
    NewsSourceSeed(
        code="boe_news",
        name="Bank of England News RSS",
        base_url="https://www.bankofengland.co.uk/rss/news",
        terms_url="https://www.bankofengland.co.uk/rss",
    ),
    NewsSourceSeed(
        code="boc_news",
        name="Bank of Canada Press Releases RSS",
        base_url="https://www.bankofcanada.ca/content_type/press-releases/feed/",
        terms_url="https://www.bankofcanada.ca/rss-feeds/",
    ),
    NewsSourceSeed(
        code="fdic_news",
        name="FDIC Press Releases RSS",
        base_url="https://public.govdelivery.com/topics/USFDIC_26/feed.rss",
        terms_url="https://www.fdic.gov/news/press-releases",
    ),
    NewsSourceSeed(
        code="hkma_news",
        name="Hong Kong Monetary Authority Press Releases API",
        base_url="https://api.hkma.gov.hk/public/press-releases",
        terms_url="https://apidocs.hkma.gov.hk/documentation/press-releases/",
        access_type="official_api",
    ),
    NewsSourceSeed(
        code="ofac_news",
        name="OFAC Recent Actions GovDelivery RSS",
        base_url="https://public.govdelivery.com/topics/USTREAS_61/feed.rss",
        terms_url="https://ofac.treasury.gov/recent-actions",
    ),
    NewsSourceSeed(
        code="gdelt_discovery",
        name="GDELT DOC 2.0 Discovery",
        base_url="https://api.gdeltproject.org/api/v2/doc/doc",
        terms_url="https://www.gdeltproject.org/about.html",
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

FRED_GLOBAL_V2_INDICATORS = tuple(
    IndicatorSeed(
        code=f"{slug}_fx_30d_change",
        provider_series_id=series,
        name=f"{label} FX 30-Day Change",
        name_ru=f"Изменение валютного курса — {label_ru}, 30 дней",
        group_code=f"{slug}_market_conditions",
        region_code=region,
        unit="percent",
        frequency="daily",
        max_staleness_seconds=7 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("5"),
            danger=Decimal("10"),
            critical=Decimal("15"),
            direction=RiskDirection.TWO_SIDED,
        ),
        transform="change_30d",
    )
    for slug, region, series, label, label_ru in (
        ("canada", "CAN", "DEXCAUS", "Canadian dollar", "канадский доллар"),
        ("uk", "GBR", "DEXUSUK", "British pound", "фунт стерлингов"),
        ("china", "CHN", "DEXCHUS", "Chinese yuan", "китайский юань"),
        ("hong_kong", "HKG", "DEXHKUS", "Hong Kong dollar", "гонконгский доллар"),
        ("japan", "JPN", "DEXJPUS", "Japanese yen", "японская иена"),
        ("korea", "KOR", "DEXKOUS", "Korean won", "южнокорейская вона"),
        ("india", "IND", "DEXINUS", "Indian rupee", "индийская рупия"),
        ("brazil", "BRA", "DEXBZUS", "Brazilian real", "бразильский реал"),
        ("mexico", "MEX", "DEXMXUS", "Mexican peso", "мексиканский песо"),
    )
)

FRED_V11_DEPTH_INDICATORS = (
    IndicatorSeed(
        code="us_continuing_claims",
        provider_series_id="CCSA",
        name="US Continuing Unemployment Claims",
        name_ru="Повторные заявки на пособие по безработице в США",
        group_code="labor",
        region_code="US",
        unit="persons",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("1900000"), danger=Decimal("2500000"), critical=Decimal("4000000"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_payrolls_monthly_change",
        provider_series_id="PAYEMS",
        name="US Nonfarm Payrolls Monthly Change",
        name_ru="Изменение занятости вне сельского хозяйства США за месяц",
        group_code="labor",
        region_code="US",
        unit="thousand_persons",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("100"), danger=Decimal("0"), critical=Decimal("-300"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="difference_1_period",
    ),
    IndicatorSeed(
        code="us_job_openings_90d_change",
        provider_series_id="JTSJOL",
        name="US Job Openings 90-Day Change",
        name_ru="Изменение числа вакансий в США за 90 дней",
        group_code="labor",
        region_code="US",
        unit="percent",
        frequency="monthly",
        max_staleness_seconds=60 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-5"), danger=Decimal("-10"), critical=Decimal("-20"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="change_90d",
    ),
    IndicatorSeed(
        code="us_quits_rate",
        provider_series_id="JTSQUR",
        name="US Quits Rate",
        name_ru="Доля добровольных увольнений в США",
        group_code="labor",
        region_code="US",
        unit="percent",
        frequency="monthly",
        max_staleness_seconds=60 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("2.0"), danger=Decimal("1.6"), critical=Decimal("1.2"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_temporary_employment_90d_change",
        provider_series_id="TEMPHELPS",
        name="US Temporary Help Employment 90-Day Change",
        name_ru="Изменение временной занятости в США за 90 дней",
        group_code="labor",
        region_code="US",
        unit="percent",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-2"), danger=Decimal("-5"), critical=Decimal("-10"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="change_90d",
    ),
    IndicatorSeed(
        code="us_manufacturing_weekly_hours",
        provider_series_id="AWHMAN",
        name="US Manufacturing Average Weekly Hours",
        name_ru="Средняя рабочая неделя в промышленности США",
        group_code="labor",
        region_code="US",
        unit="hours",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("40.5"), danger=Decimal("40.0"), critical=Decimal("39.0"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_ig_oas",
        provider_series_id="BAMLC0A0CM",
        name="US Investment-Grade Corporate Option-Adjusted Spread",
        name_ru="Спред корпоративных облигаций инвестиционного уровня США",
        group_code="credit",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("2"), danger=Decimal("3"), critical=Decimal("5"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_bank_deposits_90d_change",
        provider_series_id="DPSACBW027SBOG",
        name="US Commercial Bank Deposits 90-Day Change",
        name_ru="Изменение депозитов коммерческих банков США за 90 дней",
        group_code="banking_stress",
        region_code="US",
        unit="percent",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-1"), danger=Decimal("-3"), critical=Decimal("-6"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="change_90d",
    ),
    IndicatorSeed(
        code="us_primary_credit_borrowing",
        provider_series_id="WLCFLPCL",
        name="Federal Reserve Primary Credit Borrowing",
        name_ru="Заимствования банков через primary credit ФРС",
        group_code="banking_stress",
        region_code="US",
        unit="million_usd",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("1000"), danger=Decimal("10000"), critical=Decimal("50000"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_housing_permits_90d_change",
        provider_series_id="PERMIT",
        name="US Housing Permits 90-Day Change",
        name_ru="Изменение разрешений на строительство жилья в США за 90 дней",
        group_code="housing_cre",
        region_code="US",
        unit="percent",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-5"), danger=Decimal("-15"), critical=Decimal("-30"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="change_90d",
    ),
    IndicatorSeed(
        code="us_financial_stress_index",
        provider_series_id="STLFSI4",
        name="St. Louis Fed Financial Stress Index",
        name_ru="Индекс финансового стресса ФРБ Сент-Луиса",
        group_code="market_stress",
        region_code="US",
        unit="index",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("0"), danger=Decimal("1"), critical=Decimal("2.5"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_10y_real_yield",
        provider_series_id="DFII10",
        name="US 10-Year Real Treasury Yield",
        name_ru="Реальная доходность 10-летних облигаций США",
        group_code="rates_liquidity",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("2"), danger=Decimal("2.75"), critical=Decimal("3.5"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="broad_usd_30d_change",
        provider_series_id="DTWEXBGS",
        name="Broad US Dollar Index 30-Day Change",
        name_ru="Изменение широкого индекса доллара США за 30 дней",
        group_code="dollar_liquidity",
        region_code="GLOBAL",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("3"), danger=Decimal("6"), critical=Decimal("10"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
        transform="change_30d",
    ),
)


# These series are collected as disabled research inputs for the next immutable
# methodology version.  They must not silently change candidate-v11 scoring or
# its checksum.  Promotion requires thresholds, replay and a new methodology ID.
FRED_V12_RESEARCH_INDICATORS = (
    ResearchIndicatorSeed(
        code="us_initial_claims",
        provider_series_id="ICSA",
        name="US Initial Unemployment Claims",
        group_code="labor_research",
        region_code="US",
        unit="persons",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
    ),
    ResearchIndicatorSeed(
        code="us_unemployment_rate",
        provider_series_id="UNRATE",
        name="US Unemployment Rate",
        group_code="labor_research",
        region_code="US",
        unit="percent",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
    ),
    ResearchIndicatorSeed(
        code="us_sloos_ci_tightening",
        provider_series_id="DRTSCILM",
        name="US Banks Tightening C&I Lending Standards",
        group_code="credit_research",
        region_code="US",
        unit="percent",
        frequency="quarterly",
        max_staleness_seconds=120 * 86400,
    ),
    ResearchIndicatorSeed(
        code="us_cre_delinquency_rate",
        provider_series_id="DRCRELEXFACBS",
        name="US Commercial Real Estate Loan Delinquency Rate",
        group_code="housing_cre_research",
        region_code="US",
        unit="percent",
        frequency="quarterly",
        max_staleness_seconds=120 * 86400,
    ),
    ResearchIndicatorSeed(
        code="us_housing_starts_90d_change",
        provider_series_id="HOUST",
        name="US Housing Starts 90-Day Change",
        group_code="housing_cre_research",
        region_code="US",
        unit="percent",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
        transform="change_90d",
    ),
    ResearchIndicatorSeed(
        code="fed_liquidity_swaps",
        provider_series_id="SWPT",
        name="Federal Reserve Central-Bank Liquidity Swaps",
        group_code="dollar_liquidity_research",
        region_code="GLOBAL",
        unit="million_usd",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
    ),
    ResearchIndicatorSeed(
        code="nasdaq_composite_30d_drawdown",
        provider_series_id="NASDAQCOM",
        name="NASDAQ Composite 30-Day Drawdown",
        group_code="technology_market_research",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        transform="drawdown_30d",
    ),
    ResearchIndicatorSeed(
        code="nasdaq_100_30d_drawdown",
        provider_series_id="NASDAQ100",
        name="NASDAQ-100 30-Day Drawdown",
        group_code="technology_market_research",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        transform="drawdown_30d",
    ),
    ResearchIndicatorSeed(
        code="brent_90d_change",
        provider_series_id="DCOILBRENTEU",
        name="Brent Crude Oil Price 90-Day Change",
        group_code="commodity_research",
        region_code="GLOBAL",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=10 * 86400,
        transform="change_90d",
    ),
    ResearchIndicatorSeed(
        code="henry_hub_gas_90d_change",
        provider_series_id="DHHNGSP",
        name="Henry Hub Natural Gas Price 90-Day Change",
        group_code="commodity_research",
        region_code="GLOBAL",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=10 * 86400,
        transform="change_90d",
    ),
)


# Historical availability is an ingestion contract, not a scoring parameter.
# Keep it outside IndicatorSeed so changing provider archive capability cannot
# mutate an already-issued methodology checksum.
FRED_HISTORICAL_BACKFILL_MODES = {
    "sp500_30d_drawdown": "live_only",
}


# The ten collection-only series above enter scoring only through this new,
# immutable candidate.  Keeping separate IndicatorSeed objects makes every
# threshold, direction, Russian label and dependency assignment checksum-bound
# without mutating candidate-v11.
FRED_V12_CANDIDATE_INDICATORS = (
    IndicatorSeed(
        code="us_initial_claims",
        provider_series_id="ICSA",
        name="US Initial Unemployment Claims",
        name_ru="Первичные заявки на пособие по безработице в США",
        group_code="labor",
        region_code="US",
        unit="persons",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("300000"), danger=Decimal("450000"),
            critical=Decimal("650000"), reference=Decimal("220000"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_unemployment_rate",
        provider_series_id="UNRATE",
        name="US Unemployment Rate",
        name_ru="Уровень безработицы в США",
        group_code="labor",
        region_code="US",
        unit="percent",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("4.5"), danger=Decimal("6"),
            critical=Decimal("9"), reference=Decimal("3.5"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_sloos_ci_tightening",
        provider_series_id="DRTSCILM",
        name="US Banks Tightening C&I Lending Standards",
        name_ru="Доля банков США, ужесточающих стандарты C&I-кредитования",
        group_code="credit",
        region_code="US",
        unit="percent",
        frequency="quarterly",
        max_staleness_seconds=120 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("20"), danger=Decimal("40"),
            critical=Decimal("60"), reference=Decimal("0"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_cre_delinquency_rate",
        provider_series_id="DRCRELEXFACBS",
        name="US Commercial Real Estate Loan Delinquency Rate",
        name_ru="Просрочка по кредитам на коммерческую недвижимость США",
        group_code="housing_cre",
        region_code="US",
        unit="percent",
        frequency="quarterly",
        max_staleness_seconds=120 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("2"), danger=Decimal("4"),
            critical=Decimal("7"), reference=Decimal("1"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="us_housing_starts_90d_change",
        provider_series_id="HOUST",
        name="US Housing Starts 90-Day Change",
        name_ru="Изменение числа начатых строительств жилья в США за 90 дней",
        group_code="housing_cre",
        region_code="US",
        unit="percent",
        frequency="monthly",
        max_staleness_seconds=45 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-5"), danger=Decimal("-15"),
            critical=Decimal("-30"), reference=Decimal("5"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="change_90d",
    ),
    IndicatorSeed(
        code="fed_liquidity_swaps",
        provider_series_id="SWPT",
        name="Federal Reserve Central-Bank Liquidity Swaps",
        name_ru="Свопы ликвидности ФРС с иностранными центральными банками",
        group_code="dollar_liquidity",
        region_code="GLOBAL",
        unit="million_usd",
        frequency="weekly",
        max_staleness_seconds=14 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("1000"), danger=Decimal("10000"),
            critical=Decimal("50000"), reference=Decimal("0"),
            direction=RiskDirection.HIGHER_IS_WORSE,
        ),
    ),
    IndicatorSeed(
        code="nasdaq_composite_30d_drawdown",
        provider_series_id="NASDAQCOM",
        name="NASDAQ Composite 30-Day Drawdown",
        name_ru="Просадка NASDAQ Composite от 30-дневного максимума",
        group_code="technology_market",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-10"), danger=Decimal("-20"),
            critical=Decimal("-35"), reference=Decimal("0"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="drawdown_30d",
    ),
    IndicatorSeed(
        code="nasdaq_100_30d_drawdown",
        provider_series_id="NASDAQ100",
        name="NASDAQ-100 30-Day Drawdown",
        name_ru="Просадка NASDAQ-100 от 30-дневного максимума",
        group_code="technology_market",
        region_code="US",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=4 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("-10"), danger=Decimal("-20"),
            critical=Decimal("-35"), reference=Decimal("0"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
        transform="drawdown_30d",
    ),
    IndicatorSeed(
        code="brent_90d_change",
        provider_series_id="DCOILBRENTEU",
        name="Brent Crude Oil Price 90-Day Change",
        name_ru="Изменение цены нефти Brent за 90 дней",
        group_code="inflation_commodities",
        region_code="GLOBAL",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=10 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("25"), danger=Decimal("50"),
            critical=Decimal("100"), reference=Decimal("0"),
            direction=RiskDirection.TWO_SIDED,
        ),
        transform="change_90d",
    ),
    IndicatorSeed(
        code="henry_hub_gas_90d_change",
        provider_series_id="DHHNGSP",
        name="Henry Hub Natural Gas Price 90-Day Change",
        name_ru="Изменение цены газа Henry Hub за 90 дней",
        group_code="inflation_commodities",
        region_code="GLOBAL",
        unit="percent",
        frequency="daily",
        max_staleness_seconds=10 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("30"), danger=Decimal("75"),
            critical=Decimal("150"), reference=Decimal("0"),
            direction=RiskDirection.TWO_SIDED,
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


_WORLD_BANK_REGION_ROWS = (
    ("canada", "CAN", "Canada", "Канады"),
    ("uk", "GBR", "United Kingdom", "Великобритании"),
    ("hong_kong", "HKG", "Hong Kong SAR", "Гонконга"),
    ("japan", "JPN", "Japan", "Японии"),
    ("korea", "KOR", "Korea", "Южной Кореи"),
    ("india", "IND", "India", "Индии"),
    ("brazil", "BRA", "Brazil", "Бразилии"),
    ("mexico", "MEX", "Mexico", "Мексики"),
)

WORLD_BANK_GLOBAL_V2_INDICATORS = tuple(
    IndicatorSeed(
        code=f"{slug}_real_gdp_yoy",
        provider_series_id=f"{iso}:NY.GDP.MKTP.KD.ZG",
        name=f"{label} Real GDP Growth (Annual)",
        name_ru=f"Рост реального ВВП {label_ru} за год",
        group_code=f"{slug}_growth",
        region_code=iso,
        unit="percent",
        frequency="annual",
        max_staleness_seconds=800 * 86400,
        thresholds=IndicatorThresholds(
            warning=Decimal("1.5"),
            danger=Decimal("0"),
            critical=Decimal("-2"),
            reference=Decimal("3"),
            direction=RiskDirection.LOWER_IS_WORSE,
        ),
    )
    for slug, iso, label, label_ru in _WORLD_BANK_REGION_ROWS
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

BIS_GLOBAL_V2_INDICATORS = tuple(
    IndicatorSeed(
        code=f"{slug}_credit_to_gdp_gap",
        provider_series_id=f"WS_CREDIT_GAP:{iso}:P:A:C",
        name=f"{label} Private-Sector Credit-to-GDP Gap",
        name_ru=f"Кредитный разрыв частного сектора — {label_ru}",
        group_code=f"{slug}_credit_cycle",
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
    for slug, iso, label, label_ru in (
        ("canada", "CA", "Canada", "Канада"),
        ("uk", "GB", "United Kingdom", "Великобритания"),
        ("japan", "JP", "Japan", "Япония"),
        ("korea", "KR", "Korea", "Южная Корея"),
        ("india", "IN", "India", "Индия"),
        ("brazil", "BR", "Brazil", "Бразилия"),
        ("mexico", "MX", "Mexico", "Мексика"),
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

OECD_GLOBAL_V2_INDICATORS = tuple(
    IndicatorSeed(
        code=f"{slug}_cli_6m_change",
        provider_series_id=f"DSD_STES@DF_CLI:{area}:M:LI:AA:H:6m_change",
        name=f"{label} Composite Leading Indicator 6-Month Change",
        name_ru=f"Изменение опережающего индикатора — {label_ru}, 6 месяцев",
        group_code=f"{slug}_leading_cycle",
        region_code=area,
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
    )
    for slug, area, label, label_ru in (
        ("canada", "CAN", "Canada", "Канада"),
        ("uk", "GBR", "United Kingdom", "Великобритания"),
        ("japan", "JPN", "Japan", "Япония"),
        ("korea", "KOR", "Korea", "Южная Корея"),
        ("india", "IND", "India", "Индия"),
        ("brazil", "BRA", "Brazil", "Бразилия"),
        ("mexico", "MEX", "Mexico", "Мексика"),
        ("us", "USA", "United States", "США"),
    )
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

BYBIT_SIGNED_V11_INDICATORS = tuple(
    indicator
    for symbol, label in (("btc", "Bitcoin"), ("eth", "Ethereum"))
    for indicator in (
        IndicatorSeed(
            code=f"{symbol}_oi_1d_change",
            provider_series_id=f"{symbol.upper()}USDT:oi:1d_change",
            name=f"{label} Open Interest 1-Day Signed Change",
            name_ru=f"Изменение OI {symbol.upper()} за 1 день со знаком",
            group_code="crypto_leverage",
            region_code="CRYPTO",
            unit="percent",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
            thresholds=IndicatorThresholds(
                warning=Decimal("5"), danger=Decimal("10"), critical=Decimal("20"),
                direction=RiskDirection.TWO_SIDED,
            ),
        ),
        IndicatorSeed(
            code=f"{symbol}_oi_7d_change",
            provider_series_id=f"{symbol.upper()}USDT:oi:7d_change",
            name=f"{label} Open Interest 7-Day Signed Change",
            name_ru=f"Изменение OI {symbol.upper()} за 7 дней со знаком",
            group_code="crypto_leverage",
            region_code="CRYPTO",
            unit="percent",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
            thresholds=IndicatorThresholds(
                warning=Decimal("15"), danger=Decimal("25"), critical=Decimal("40"),
                direction=RiskDirection.TWO_SIDED,
            ),
        ),
        IndicatorSeed(
            code=f"{symbol}_oi_30d_change",
            provider_series_id=f"{symbol.upper()}USDT:oi:30d_change",
            name=f"{label} Open Interest 30-Day Signed Change",
            name_ru=f"Изменение OI {symbol.upper()} за 30 дней со знаком",
            group_code="crypto_leverage",
            region_code="CRYPTO",
            unit="percent",
            frequency="daily",
            max_staleness_seconds=2 * 86400,
            thresholds=IndicatorThresholds(
                warning=Decimal("25"), danger=Decimal("50"), critical=Decimal("80"),
                direction=RiskDirection.TWO_SIDED,
            ),
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


_V2_THRESHOLDS = {
    "sp500_30d_drawdown": IndicatorThresholds(
        warning=Decimal("-10"), danger=Decimal("-20"), critical=Decimal("-30"),
        direction=RiskDirection.LOWER_IS_WORSE,
    ),
    "us_nfci": IndicatorThresholds(
        warning=Decimal("0"), danger=Decimal("0.5"), critical=Decimal("1"),
        reference=Decimal("-0.5"), direction=RiskDirection.HIGHER_IS_WORSE,
    ),
    "fed_assets_90d_change": IndicatorThresholds(
        warning=Decimal("-3"), danger=Decimal("-6"), critical=Decimal("-10"),
        reference=Decimal("2"), direction=RiskDirection.LOWER_IS_WORSE,
    ),
    "euro_real_gdp_qoq": IndicatorThresholds(
        warning=Decimal("0.2"), danger=Decimal("0"), critical=Decimal("-1"),
        reference=Decimal("0.5"), direction=RiskDirection.LOWER_IS_WORSE,
    ),
    "china_real_gdp_yoy": IndicatorThresholds(
        warning=Decimal("4"), danger=Decimal("3"), critical=Decimal("1"),
        reference=Decimal("6"), direction=RiskDirection.LOWER_IS_WORSE,
    ),
    "btc_oi_7d_abs_change": IndicatorThresholds(
        warning=Decimal("15"), danger=Decimal("25"), critical=Decimal("40"),
        direction=RiskDirection.HIGHER_IS_WORSE,
    ),
    "eth_oi_7d_abs_change": IndicatorThresholds(
        warning=Decimal("15"), danger=Decimal("25"), critical=Decimal("40"),
        direction=RiskDirection.HIGHER_IS_WORSE,
    ),
}

V2_INDICATORS = tuple(
    replace(item, thresholds=_V2_THRESHOLDS.get(item.code, item.thresholds))
    for item in STARTER_INDICATORS
)

GLOBAL_V2_INDICATORS = (
    V2_INDICATORS
    + FRED_GLOBAL_V2_INDICATORS
    + WORLD_BANK_GLOBAL_V2_INDICATORS
    + BIS_GLOBAL_V2_INDICATORS
    + OECD_GLOBAL_V2_INDICATORS
)

V11_INDICATORS = tuple(
    item for item in GLOBAL_V2_INDICATORS if not item.code.endswith("_oi_7d_abs_change")
) + FRED_V11_DEPTH_INDICATORS + BYBIT_SIGNED_V11_INDICATORS
V12_INDICATORS = V11_INDICATORS + FRED_V12_CANDIDATE_INDICATORS
V13_INDICATORS = V12_INDICATORS

_V11_SCENARIO_EXTRA_GROUPS = {
    "global_recession": ("housing_cre",),
    "financial_stress": ("banking_stress", "dollar_liquidity"),
    "regional_recession": ("housing_cre",),
    "banking_crisis": ("banking_stress", "dollar_liquidity"),
    "sovereign_currency_crisis": ("dollar_liquidity",),
    "tech_ai_repricing": ("dollar_liquidity",),
}
V11_SCENARIOS = tuple(
    replace(
        scenario,
        group_codes=scenario.group_codes + _V11_SCENARIO_EXTRA_GROUPS.get(scenario.code, ()),
        anchor_groups=(
            scenario.anchor_groups + ("banking_stress",)
            if scenario.code == "banking_crisis"
            else scenario.anchor_groups
        ),
    )
    for scenario in V2_SCENARIOS
)
V12_SCENARIOS = tuple(
    replace(
        scenario,
        group_codes=(
            scenario.group_codes + ("technology_market",)
            if scenario.code == "tech_ai_repricing"
            else scenario.group_codes
        ),
        anchor_groups=(
            scenario.anchor_groups + ("technology_market",)
            if scenario.code == "tech_ai_repricing"
            else scenario.anchor_groups
        ),
    )
    for scenario in V11_SCENARIOS
)

_FINANCIAL_STRESS_REGIONAL_GROUPS = (
    "canada_market_conditions",
    "uk_market_conditions",
    "china_market_conditions",
    "hong_kong_market_conditions",
    "japan_market_conditions",
    "korea_market_conditions",
    "india_market_conditions",
    "brazil_market_conditions",
    "mexico_market_conditions",
)
V13_REPLAY_COVERAGE_CONTRACT = {
    "version": "scenario-replay-coverage-v1",
    "coverage_unit": "scenario_group_max_freshness",
    "minimum_coverage": "0.70",
    "healthy_coverage": "0.85",
    "scenarios": {
        "financial_stress": {
            "required_channel_classes": {
                "credit": ("credit",),
                "market_price_stress": ("market_stress", "equity_market_stress"),
                "funding_liquidity": (
                    "rates_liquidity",
                    "us_financial_conditions",
                    "banking_stress",
                    "dollar_liquidity",
                ),
            },
            "required_region_classes": {
                "us": {"alternatives": ("US",), "minimum_matches": 1},
                "other_advanced": {
                    "alternatives": ("EU", "CAN", "GBR", "HKG", "JPN", "KOR"),
                    "minimum_matches": 2,
                },
                "emerging": {
                    "alternatives": ("CHINA", "IND", "BRA", "MEX"),
                    "minimum_matches": 2,
                },
            },
        },
    },
}
V13_SCENARIOS = tuple(
    replace(
        scenario,
        group_codes=(
            scenario.group_codes + _FINANCIAL_STRESS_REGIONAL_GROUPS
            if scenario.code == "financial_stress"
            else scenario.group_codes
        ),
    )
    for scenario in V12_SCENARIOS
)

_V2_THRESHOLD_RATIONALE = {
    "sahm_rule": {
        "ru": "0,50 — официальный триггер Sahm Rule; 0,25 и 1,00 — внутренние уровни.",
        "en": "0.50 is the official Sahm trigger; 0.25 and 1.00 are internal bands.",
        "source_url": "https://fred.stlouisfed.org/release?rid=456",
        "operational_role": "recession_confirmation",
    },
    "us_credit_to_gdp_gap": {
        "ru": "2/10 — Basel guide накопленной уязвимости; не самостоятельный кризисный триггер.",
        "en": "2/10 is the Basel vulnerability guide, not a standalone crisis trigger.",
        "source_url": "https://www.bis.org/publ/qtrpdf/r_qt1403g.htm",
        "operational_role": "structural_vulnerability",
    },
    "china_credit_to_gdp_gap": {
        "ru": "2/10 — Basel guide накопленной уязвимости; не самостоятельный кризисный триггер.",
        "en": "2/10 is the Basel vulnerability guide, not a standalone crisis trigger.",
        "source_url": "https://www.bis.org/publ/qtrpdf/r_qt1403g.htm",
        "operational_role": "structural_vulnerability",
    },
    "world_real_gdp_yoy": {
        "ru": "Годовой ряд используется как структурный контекст, а не оперативный триггер.",
        "en": "The annual series is structural context, not an operational trigger.",
        "operational_role": "lagging_context",
    },
    "fed_assets_90d_change": {
        "ru": "Сжатие — уязвимость ликвидности; экстренное расширение будет отдельным реактивным признаком.",
        "en": "Contraction is a liquidity vulnerability; emergency expansion is a separate reaction feature.",
        "operational_role": "liquidity_context",
    },
    "us_initial_claims": {
        "ru": "300/450/650 тыс. отделяют раннее ухудшение труда от рецессионного и кризисного потока заявок; уровни являются кандидатами для replay.",
        "en": "300k/450k/650k separate early labor deterioration from recessionary and crisis claims flows; levels remain replay candidates.",
        "source_url": "https://fred.stlouisfed.org/series/ICSA",
        "operational_role": "leading_labor_stress",
    },
    "us_unemployment_rate": {
        "ru": "4,5/6/9% описывают напряжение, рецессионную слабость и тяжёлый стресс; динамика и Sahm Rule остаются независимыми подтверждениями.",
        "en": "4.5%/6%/9% represent tension, recessionary weakness and severe stress; momentum and the Sahm Rule remain separate confirmations.",
        "source_url": "https://fred.stlouisfed.org/series/UNRATE",
        "operational_role": "lagging_labor_confirmation",
    },
    "us_sloos_ci_tightening": {
        "ru": "20/40/60% net tightening — внутренние кандидатные уровни ограничений предложения корпоративного кредита.",
        "en": "20%/40%/60% net tightening are internal candidate levels for constrained corporate credit supply.",
        "source_url": "https://fred.stlouisfed.org/series/DRTSCILM",
        "operational_role": "credit_supply_confirmation",
    },
    "us_cre_delinquency_rate": {
        "ru": "2/4/7% — кандидатные уровни растущей просрочки CRE; ряд квартальный и не используется как быстрый одиночный триггер.",
        "en": "2%/4%/7% are candidate CRE delinquency bands; the quarterly series is not a fast standalone trigger.",
        "source_url": "https://fred.stlouisfed.org/series/DRCRELEXFACBS",
        "operational_role": "cre_credit_confirmation",
    },
    "us_housing_starts_90d_change": {
        "ru": "−5/−15/−30% — кандидатные уровни охлаждения чувствительного к ставкам жилищного цикла.",
        "en": "-5%/-15%/-30% are candidate levels for deterioration in the rate-sensitive housing cycle.",
        "source_url": "https://fred.stlouisfed.org/series/HOUST",
        "operational_role": "leading_housing_activity",
    },
    "fed_liquidity_swaps": {
        "ru": "$1/10/50 млрд — кандидатные уровни реактивного спроса иностранных центральных банков на долларовую ликвидность.",
        "en": "$1bn/$10bn/$50bn are candidate levels of reactive foreign-central-bank demand for dollar liquidity.",
        "source_url": "https://fred.stlouisfed.org/series/SWPT",
        "operational_role": "emergency_dollar_liquidity",
    },
    "nasdaq_composite_30d_drawdown": {
        "ru": "−10/−20/−35% — candidate drawdown bands; NASDAQ-100 находится в том же dependency-подканале и не удваивает ширину.",
        "en": "-10%/-20%/-35% are candidate drawdown bands; NASDAQ-100 shares the same dependency subchannel and cannot double breadth.",
        "source_url": "https://fred.stlouisfed.org/series/NASDAQCOM",
        "operational_role": "technology_market_stress",
    },
    "nasdaq_100_30d_drawdown": {
        "ru": "−10/−20/−35% — candidate drawdown bands; сигнал подтверждает, но не дублирует NASDAQ Composite.",
        "en": "-10%/-20%/-35% are candidate drawdown bands; the signal confirms but does not duplicate NASDAQ Composite.",
        "source_url": "https://fred.stlouisfed.org/series/NASDAQ100",
        "operational_role": "technology_market_confirmation",
    },
    "brent_90d_change": {
        "ru": "Абсолютные изменения 25/50/100% отмечают как инфляционный скачок, так и рецессионный обвал; направление объясняется сценарием.",
        "en": "Absolute 25%/50%/100% changes capture both inflationary spikes and recessionary collapses; scenario context explains direction.",
        "source_url": "https://fred.stlouisfed.org/series/DCOILBRENTEU",
        "operational_role": "two_sided_energy_shock",
    },
    "henry_hub_gas_90d_change": {
        "ru": "Абсолютные изменения 30/75/150% — кандидатные уровни более волатильного газового шока; нулевая база исключается.",
        "en": "Absolute 30%/75%/150% changes are candidate levels for the more volatile gas shock; zero bases are excluded.",
        "source_url": "https://fred.stlouisfed.org/series/DHHNGSP",
        "operational_role": "two_sided_energy_shock",
    },
}


def methodology_checksum(
    *,
    version: str = METHODOLOGY_VERSION,
    indicators: tuple[IndicatorSeed, ...] = STARTER_INDICATORS,
    scenarios: tuple[ScenarioDefinition, ...] = SCENARIOS,
) -> str:
    # ``starter-v8`` is already persisted in user and production databases.  Its
    # digest must remain immutable even when shared source metadata is enriched
    # for later candidate methodologies.  Keeping the released digest here is
    # the database-compatible equivalent of a signed migration artifact.
    if (
        version == METHODOLOGY_VERSION
        and indicators == STARTER_INDICATORS
        and scenarios == SCENARIOS
    ):
        return "741836721273b55035706e237cf5fdfe8559c889e6ccc33cac2bb6a82073d742"
    payload = {
        "methodology": [METHODOLOGY_CODE, version],
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
            for item in indicators
        ],
        "scenarios": [asdict(item) for item in scenarios],
        "stability": {
            **asdict(STABILITY_POLICY),
            "recovery_fraction": str(STABILITY_POLICY.recovery_fraction),
        },
    }
    if version in {
        METHODOLOGY_V11_VERSION,
        METHODOLOGY_V12_VERSION,
        METHODOLOGY_V13_VERSION,
    }:
        payload["indicator_scoring"] = {
            code: {key: str(value) for key, value in asdict(profile).items()}
            for code, profile in PROFILES.items()
        }
        payload["dependency_graph_version"] = DEPENDENCY_GRAPH_VERSION
        payload["stage_version"] = STAGE_VERSION
    if version == METHODOLOGY_V13_VERSION:
        payload["replay_coverage_contract"] = V13_REPLAY_COVERAGE_CONTRACT
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_code_for_indicator(code: str) -> str:
    for source, indicators in (
        (FRED, FRED_INDICATORS),
        (FRED, FRED_GLOBAL_V2_INDICATORS),
        (FRED, FRED_V11_DEPTH_INDICATORS),
        (FRED, FRED_V12_CANDIDATE_INDICATORS),
        (BEA, BEA_INDICATORS),
        (EIA, EIA_INDICATORS),
        (ECB, ECB_INDICATORS),
        (EUROSTAT, EUROSTAT_INDICATORS),
        (WORLD_BANK, WORLD_BANK_INDICATORS),
        (BIS, BIS_INDICATORS),
        (OECD, OECD_INDICATORS),
        (BYBIT, BYBIT_INDICATORS),
        (BYBIT, BYBIT_SIGNED_V11_INDICATORS),
        (WORLD_BANK, WORLD_BANK_GLOBAL_V2_INDICATORS),
        (BIS, BIS_GLOBAL_V2_INDICATORS),
        (OECD, OECD_GLOBAL_V2_INDICATORS),
    ):
        if any(item.code == code for item in indicators):
            return source.code
    raise ValueError(f"unknown indicator source: {code}")


def _bootstrap_catalog(
    repository: CrisisRadarRepository,
    *,
    version: str,
    indicators: tuple[IndicatorSeed, ...],
    scenarios: tuple[ScenarioDefinition, ...],
    promotion_status: str,
    indicator_enabled_overrides: dict[str, bool] | None = None,
) -> dict[str, int | str]:
    methodology_id = repository.register_methodology(
        METHODOLOGY_CODE,
        version,
        checksum=methodology_checksum(
            version=version,
            indicators=indicators,
            scenarios=scenarios,
        ),
        effective_from=(
            "2026-08-05T12:53:16+00:00"
            if version == METHODOLOGY_V11_VERSION
            else "2026-08-13T11:27:28+00:00"
            if version == METHODOLOGY_V12_VERSION
            else "2026-08-13T13:05:00+00:00"
            if version == METHODOLOGY_V13_VERSION
            else
            "2026-08-04T12:00:00+00:00"
            if version == METHODOLOGY_GLOBAL_V2_VERSION
            else "2026-08-04T00:00:00+00:00"
            if version == METHODOLOGY_V2_VERSION
            else "2026-07-21T00:00:00+00:00"
        ),
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
            access_type=(
                "discovery_api"
                if source.code == "gdelt_discovery"
                else source.access_type
            ),
            expected_frequency="intraday",
            max_staleness_seconds=2 * 86400,
        )
    for item in indicators:
        source_code = _source_code_for_indicator(item.code)
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
            enabled=(indicator_enabled_overrides or {}).get(item.code, True),
        )
        rationale = _V2_THRESHOLD_RATIONALE.get(
            item.code,
            {
                "ru": "Стартовый кандидат; требуется shadow replay и sensitivity analysis.",
                "en": "Seed candidate requiring shadow replay and sensitivity analysis.",
                "operational_role": "candidate_signal",
            },
        )
        is_advanced = version in {
            METHODOLOGY_V11_VERSION,
            METHODOLOGY_V12_VERSION,
            METHODOLOGY_V13_VERSION,
        }
        profile = profile_for(
            frequency=item.frequency,
            direction=item.thresholds.direction,
            code=item.code,
        )
        source = next(
            source
            for source in (FRED, BEA, EIA, ECB, EUROSTAT, WORLD_BANK, BIS, OECD, BYBIT)
            if source.code == source_code
        )
        repository.register_thresholds(
            indicator_id,
            methodology_id,
            item.thresholds,
            basis="hybrid" if version in {
                METHODOLOGY_V2_VERSION,
                METHODOLOGY_V11_VERSION,
                METHODOLOGY_V12_VERSION,
                METHODOLOGY_V13_VERSION,
            } else "legacy",
            promotion_status=promotion_status,
            rationale=rationale if version in {
                METHODOLOGY_V2_VERSION,
                METHODOLOGY_V11_VERSION,
                METHODOLOGY_V12_VERSION,
                METHODOLOGY_V13_VERSION,
            } else {},
            source_url=(
                rationale.get("source_url")
                or (
                    f"https://fred.stlouisfed.org/series/{item.provider_series_id}"
                    if source_code == FRED.code and ":" not in item.provider_series_id
                    else source.base_url
                )
            ) if is_advanced else "",
            operational_role=(
                str(rationale.get("operational_role") or "candidate_signal")
                if is_advanced else ""
            ),
            profile=profile.code if is_advanced else "",
            promotion_evidence={
                "status": "not_promoted",
                "required": ["causal_replay", "sensitivity", "live_canary"],
            } if is_advanced else {},
            introduced_at=(
                "2026-08-13T11:27:28+00:00"
                if version == METHODOLOGY_V12_VERSION
                else "2026-08-13T13:05:00+00:00"
                if version == METHODOLOGY_V13_VERSION
                else "2026-08-05T12:53:16+00:00"
                if version == METHODOLOGY_V11_VERSION
                else ""
            ),
        )
        if is_advanced:
            from trading_bot.crisis_radar.metadata_v11 import (
                group_metadata,
                indicator_metadata,
            )

            repository.register_entity_metadata(
                entity_type="indicator",
                entity_code=item.code,
                metadata_version=(
                    "v13"
                    if version == METHODOLOGY_V13_VERSION
                    else "v12"
                    if version == METHODOLOGY_V12_VERSION
                    else "v11"
                ),
                payload=indicator_metadata(item, source_name=source.name),
            )
            repository.register_dependency_assignment(
                methodology_id=methodology_id,
                assignment=dependency_for(
                    code=item.code,
                    group_code=item.group_code,
                    region_code=item.region_code,
                ),
                graph_version=DEPENDENCY_GRAPH_VERSION,
            )
            repository.register_entity_metadata(
                entity_type="group",
                entity_code=item.group_code,
                metadata_version=(
                    "v13"
                    if version == METHODOLOGY_V13_VERSION
                    else "v12"
                    if version == METHODOLOGY_V12_VERSION
                    else "v11"
                ),
                payload=group_metadata(item.group_code),
            )
    active_indicator_codes = {indicator.code for indicator in indicators}
    for source, research_indicators in (
        (BYBIT, BYBIT_RESEARCH_INDICATORS),
        (FRED, FRED_V12_RESEARCH_INDICATORS),
    ):
        for item in research_indicators:
            if item.code in active_indicator_codes:
                continue
            repository.register_indicator(
                item.code,
                item.name,
                group_code=item.group_code,
                unit=item.unit,
                frequency=item.frequency,
                risk_direction=RiskDirection.TWO_SIDED.value,
                source_code=source.code,
                region_code=item.region_code,
                provider_series_id=item.provider_series_id,
                transform=item.transform,
                max_staleness_seconds=item.max_staleness_seconds,
                enabled=False,
            )
    for scenario in scenarios:
        repository.register_scenario(
            scenario.code,
            methodology_id,
            name_ru=scenario.name_ru,
            name_en=scenario.name_en,
            horizon=scenario.horizon,
            group_codes=scenario.group_codes,
            anchor_groups=scenario.anchor_groups,
        )
        if version in {
            METHODOLOGY_V11_VERSION,
            METHODOLOGY_V12_VERSION,
            METHODOLOGY_V13_VERSION,
        }:
            from trading_bot.crisis_radar.metadata_v11 import scenario_metadata

            repository.register_entity_metadata(
                entity_type="scenario",
                entity_code=scenario.code,
                metadata_version=(
                    "v13"
                    if version == METHODOLOGY_V13_VERSION
                    else "v12"
                    if version == METHODOLOGY_V12_VERSION
                    else "v11"
                ),
                payload=scenario_metadata(scenario),
            )
    return {
        "methodology_id": methodology_id,
        "methodology_version": version,
        "indicator_count": len(indicators),
        "research_indicator_count": (
            len(BYBIT_RESEARCH_INDICATORS) + len(FRED_V12_RESEARCH_INDICATORS)
        ),
        "scenario_count": len(scenarios),
    }


def bootstrap_starter_catalog(repository: CrisisRadarRepository) -> dict[str, int | str]:
    return _bootstrap_catalog(
        repository,
        version=METHODOLOGY_VERSION,
        indicators=STARTER_INDICATORS,
        scenarios=SCENARIOS,
        promotion_status="active",
    )


def bootstrap_v2_catalog(repository: CrisisRadarRepository) -> dict[str, int | str]:
    return _bootstrap_catalog(
        repository,
        version=METHODOLOGY_V2_VERSION,
        indicators=V2_INDICATORS,
        scenarios=SCENARIOS,
        promotion_status="candidate",
    )


def bootstrap_global_v2_catalog(repository: CrisisRadarRepository) -> dict[str, int | str]:
    return _bootstrap_catalog(
        repository,
        version=METHODOLOGY_GLOBAL_V2_VERSION,
        indicators=GLOBAL_V2_INDICATORS,
        scenarios=V2_SCENARIOS,
        promotion_status="candidate",
    )


def bootstrap_v11_catalog(repository: CrisisRadarRepository) -> dict[str, int | str]:
    return _bootstrap_catalog(
        repository,
        version=METHODOLOGY_V11_VERSION,
        indicators=V11_INDICATORS,
        scenarios=V11_SCENARIOS,
        promotion_status="candidate",
    )


def bootstrap_v12_catalog(repository: CrisisRadarRepository) -> dict[str, int | str]:
    """Register a replay-only candidate without enabling its new live inputs."""

    new_codes = {item.code for item in FRED_V12_CANDIDATE_INDICATORS}
    return _bootstrap_catalog(
        repository,
        version=METHODOLOGY_V12_VERSION,
        indicators=V12_INDICATORS,
        scenarios=V12_SCENARIOS,
        promotion_status="candidate",
        indicator_enabled_overrides={code: False for code in new_codes},
    )


def bootstrap_v13_catalog(repository: CrisisRadarRepository) -> dict[str, int | str]:
    """Register scenario-specific replay coverage without enabling new live inputs."""

    new_codes = {item.code for item in FRED_V12_CANDIDATE_INDICATORS}
    return _bootstrap_catalog(
        repository,
        version=METHODOLOGY_V13_VERSION,
        indicators=V13_INDICATORS,
        scenarios=V13_SCENARIOS,
        promotion_status="candidate",
        indicator_enabled_overrides={code: False for code in new_codes},
    )
