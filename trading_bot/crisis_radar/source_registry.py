from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourcePolicy:
    code: str
    tier: str
    license_or_terms_url: str
    transport: str
    expected_frequency: str
    publication_lag: str
    rate_limit_policy: str
    fallback_code: str | None
    production_status: str
    operational_role: str

    def __post_init__(self) -> None:
        if self.tier not in {"A", "B", "C"}:
            raise ValueError("source tier must be A, B or C")
        if self.production_status not in {"active", "candidate", "discovery_only", "disabled"}:
            raise ValueError("invalid source production status")
        if not self.license_or_terms_url.startswith("https://"):
            raise ValueError("source terms must use HTTPS")


SOURCE_POLICIES = (
    SourcePolicy(
        "fred", "A", "https://fred.stlouisfed.org/docs/api/terms_of_use.html", "api-key",
        "mixed", "provider release schedule", "provider quota; bounded retries", None, "active",
        "US macro, credit and financial markets",
    ),
    SourcePolicy(
        "bea", "A", "https://apps.bea.gov/API/bea_web_service_api_user_guide.htm", "api-key",
        "quarterly", "official release", "bounded retries", "fred", "active", "US GDP vintage",
    ),
    SourcePolicy(
        "eia", "A", "https://www.eia.gov/opendata/terms.php", "api-key", "daily",
        "one or more business days", "provider quota; bounded retries", None, "active", "energy",
    ),
    SourcePolicy(
        "ecb", "A", "https://data.ecb.europa.eu/help/api/overview", "public-api", "daily",
        "provider release schedule", "bounded query and retries", None, "active", "euro financial stress",
    ),
    SourcePolicy(
        "eurostat", "A", "https://ec.europa.eu/eurostat/about-us/policies/copyright", "public-api",
        "quarterly", "official release", "bounded query and retries", None, "active", "euro growth",
    ),
    SourcePolicy(
        "world_bank", "A", "https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets",
        "public-api", "annual", "long/structural", "bounded query and retries", "oecd", "candidate",
        "structural growth context; never an intraday trigger",
    ),
    SourcePolicy(
        "bis", "A", "https://www.bis.org/terms_conditions.htm", "official-bulk", "quarterly",
        "quarterly publication lag", "three allowlisted bounded archives per daily sync", None, "candidate",
        "credit, debt-service and housing-cycle vulnerability; not a standalone crisis event",
    ),
    SourcePolicy(
        "oecd", "A", "https://www.oecd.org/en/about/terms-conditions.html", "official-sdmx", "monthly",
        "provider release schedule", "one bounded multi-area query per daily sync", "world_bank", "candidate",
        "cross-region leading cycle",
    ),
    SourcePolicy(
        "bybit", "B", "https://www.bybit.com/en/help-center/article/Bybit-Website-Terms-and-Conditions",
        "public-exchange-api", "mixed", "near real-time", "endpoint-specific bounded requests", None, "active",
        "crypto market, price and leverage",
    ),
    SourcePolicy(
        "fed_news", "A", "https://www.federalreserve.gov/feeds.htm", "official-rss", "intraday",
        "publication time", "bounded polling", None, "active", "US monetary and banking events",
    ),
    SourcePolicy(
        "ecb_news", "A", "https://www.ecb.europa.eu/services/copyright/html/index.en.html",
        "official-rss", "intraday", "publication time", "bounded polling", None, "active",
        "euro-area monetary and financial-stability events",
    ),
    SourcePolicy(
        "sec_news", "A", "https://www.sec.gov/about/rss-feeds", "official-rss", "intraday",
        "publication time", "bounded polling", None, "candidate", "US securities regulation and failures",
    ),
    SourcePolicy(
        "cftc_news", "A", "https://www.cftc.gov/RSS/index.htm", "official-rss", "intraday",
        "publication time", "bounded polling", None, "candidate", "derivatives and digital-asset regulation",
    ),
    SourcePolicy(
        "bis_news", "A", "https://www.bis.org/rss/index.htm", "official-rss", "intraday",
        "publication time", "bounded polling", None, "candidate", "global banking and financial stability",
    ),
    SourcePolicy(
        "boj_news", "A", "https://www.boj.or.jp/en/tips.htm", "official-rss", "intraday",
        "publication time", "bounded polling", None, "candidate", "Japan monetary and financial stability",
    ),
    SourcePolicy(
        "rbi_news", "A", "https://www.rbi.org.in/Scripts/rss.aspx", "official-rss", "intraday",
        "publication time", "bounded polling", None, "candidate", "India monetary and financial stability",
    ),
    SourcePolicy(
        "boe_news", "A", "https://www.bankofengland.co.uk/legal/terms", "official-rss", "intraday",
        "publication time", "bounded polling", None, "candidate", "UK monetary and financial stability",
    ),
    SourcePolicy(
        "boc_news", "A", "https://www.bankofcanada.ca/terms/", "official-rss", "intraday",
        "publication time", "bounded polling", None, "candidate", "Canada monetary and financial stability",
    ),
    SourcePolicy(
        "fdic_news", "A", "https://www.fdic.gov/policies", "official-rss", "intraday",
        "publication time", "bounded polling", None, "candidate", "US bank failures and resolutions",
    ),
    SourcePolicy(
        "hkma_news", "A", "https://apidocs.hkma.gov.hk/documentation/press-releases/",
        "official-json-api", "intraday", "publication date", "bounded polling", None,
        "candidate", "Hong Kong and Greater China banking, liquidity and renminbi events",
    ),
    SourcePolicy(
        "nbs_news", "A",
        "https://www.stats.gov.cn/wzgl/rss/202302/t20230217_1912859.html",
        "official-rss", "intraday", "official China Standard Time publication timestamp",
        "one bounded 6 MB feed request per polling interval", None, "candidate",
        "official China growth, labor, inflation, industry, demand and housing releases",
    ),
    SourcePolicy(
        "ofac_news", "A", "https://ofac.treasury.gov/recent-actions",
        "official-govdelivery-rss", "intraday", "publication time", "bounded polling",
        None, "candidate", "US Treasury sanctions, designations and restrictions",
    ),
    SourcePolicy(
        "gdelt_discovery", "C", "https://www.gdeltproject.org/about.html", "public-discovery-api", "intraday",
        "discovery lag", "one bounded query per interval", None, "discovery_only",
        "multilingual event discovery; cannot confirm an event alone",
    ),
)


def source_registry_payload() -> dict:
    return {
        "version": "2026-08-13",
        "sources": [asdict(item) for item in SOURCE_POLICIES],
        "rules": {
            "official_first": True,
            "aggregator_is_discovery_only": True,
            "html_scraping_is_required": False,
            "missing_data_is_never_forward_filled_silently": True,
        },
    }
