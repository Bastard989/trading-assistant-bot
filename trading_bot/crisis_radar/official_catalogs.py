from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_bot.crisis_radar.event_catalog import EventCatalogVersion, HistoricalEventLabel
from trading_bot.crisis_radar.repositories import CrisisRadarRepository


UTC = timezone.utc
EFFECTIVE_FROM = datetime(2026, 7, 21, tzinfo=UTC)
WORLD_BANK_RECESSIONS_URL = (
    "https://thedocs.worldbank.org/en/doc/7ce50b5aa95bef66048680bba9926ec8-"
    "0050012026/related/GEP-Jan-2026-Box-1-1.pdf"
)
OFR_STRESS_URL = (
    "https://www.financialresearch.gov/working-papers/files/"
    "OFRwp-17-04_The-OFR-Financial-Stress-Index.pdf"
)
WORLD_BANK_STAGFLATION_URL = (
    "https://thedocs.worldbank.org/en/doc/18ad707266f7740bced755498ae0307a-"
    "0350012022/related/Global-Economic-Prospects-June-2022-Topical-Issue-1.pdf"
)
BYBIT_MARKET_DOCS_URL = "https://bybit-exchange.github.io/docs/v5/market/tickers"
CHINA_NBS_URL = "https://data.stats.gov.cn/english/"


def _year_label(scenario: str, year: int, source_url: str) -> HistoricalEventLabel:
    return HistoricalEventLabel(
        code=f"{scenario}-{year}",
        started_at=datetime(year, 1, 1, tzinfo=UTC),
        ended_at=datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC),
        start_precision="year",
        end_precision="year",
        region_code="GLOBAL",
        source_url=source_url,
        source_note=(
            "The stored calendar-year bounds represent an official annual label, "
            "not an exact daily onset or trough."
        ),
    )


GLOBAL_RECESSION_CATALOG = EventCatalogVersion(
    scenario_code="global_recession",
    version="world-bank-global-recessions-2026-v1",
    source_name="World Bank Global Economic Prospects",
    source_url=WORLD_BANK_RECESSIONS_URL,
    definition={
        "target": "global_recession_year",
        "method": (
            "Contraction in annual global real GDP per capita accompanied by broad weakness "
            "in other global activity indicators; the official World Bank episode list is used."
        ),
        "coverage_start": "1960-01-01",
        "coverage_end": "2025-12-31",
        "label_precision": "year",
    },
    limitations=(
        "Annual labels cannot identify an exact daily onset or end.",
        "The catalog is a versioned official episode list, not a mechanical GDP threshold.",
        "World Bank indicator history lacks complete real-time vintages for early decades.",
    ),
    effective_from=EFFECTIVE_FROM,
    labels=tuple(
        _year_label("global-recession", year, WORLD_BANK_RECESSIONS_URL)
        for year in (1975, 1982, 1991, 2009, 2020)
    ),
)


OFR_INTERVENTION_DATES = tuple(
    datetime.fromisoformat(value).replace(tzinfo=UTC)
    for value in (
        "1998-09-23",
        "2001-09-11",
        "2007-08-10",
        "2007-08-17",
        "2007-08-21",
        "2007-11-26",
        "2007-12-12",
        "2008-03-07",
        "2008-03-11",
        "2008-03-14",
        "2008-03-16",
        "2008-05-02",
        "2008-07-13",
        "2008-07-30",
        "2008-09-07",
        "2008-09-15",
        "2008-09-16",
        "2008-09-19",
        "2008-09-28",
        "2008-10-06",
        "2008-10-07",
        "2008-10-08",
        "2008-10-14",
        "2008-10-21",
        "2008-11-23",
        "2008-11-25",
        "2008-12-30",
        "2009-01-07",
        "2009-01-16",
        "2009-01-30",
        "2009-02-25",
        "2009-03-23",
        "2009-05-01",
        "2009-05-07",
        "2009-05-19",
        "2010-05-02",
        "2010-05-09",
        "2010-05-11",
        "2010-10-29",
        "2010-11-28",
        "2011-03-11",
        "2011-05-17",
        "2011-07-21",
        "2011-08-04",
        "2011-09-06",
        "2011-10-27",
        "2011-11-30",
        "2011-12-08",
        "2012-02-21",
        "2012-03-12",
        "2012-06-09",
        "2012-06-25",
        "2014-04-30",
        "2015-01-15",
        "2015-03-11",
        "2015-08-14",
        "2016-08-04",
    )
)


def _ofr_episodes() -> tuple[HistoricalEventLabel, ...]:
    windows = [
        [anchor - timedelta(days=28), anchor + timedelta(days=28), [anchor.date().isoformat()]]
        for anchor in OFR_INTERVENTION_DATES
    ]
    merged: list[list] = []
    for started_at, ended_at, anchors in windows:
        if merged and started_at <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], ended_at)
            merged[-1][2].extend(anchors)
        else:
            merged.append([started_at, ended_at, anchors])
    return tuple(
        HistoricalEventLabel(
            code=f"ofr-stress-episode-{index:02d}",
            started_at=started_at,
            ended_at=ended_at.replace(hour=23, minute=59, second=59),
            start_precision="day",
            end_precision="day",
            region_code="GLOBAL",
            source_url=OFR_STRESS_URL,
            source_note=(
                "Derived exactly from the OFR StressEvent definition: four weeks before and "
                f"after official Appendix B intervention dates; anchors={','.join(anchors)}"
            ),
            status="derived",
        )
        for index, (started_at, ended_at, anchors) in enumerate(merged, start=1)
    )


FINANCIAL_STRESS_CATALOG = EventCatalogVersion(
    scenario_code="financial_stress",
    version="ofr-policy-interventions-2017-v1",
    source_name="US Office of Financial Research",
    source_url=OFR_STRESS_URL,
    definition={
        "target": "ofr_stress_event_proxy",
        "method": "StressEvent equals one from 28 days before through 28 days after an intervention.",
        "anchors": [item.date().isoformat() for item in OFR_INTERVENTION_DATES],
        "overlap_policy": "merge_overlapping_windows",
        "coverage_start": "1998-08-26",
        "coverage_end": "2016-09-01",
        "label_precision": "day",
    },
    limitations=(
        "This is an OFR policy-intervention proxy, not a unique true crisis date.",
        "Closely spaced intervention windows are merged and are not independent events.",
        "The published catalog ends in 2016 and does not label later stress episodes.",
    ),
    effective_from=EFFECTIVE_FROM,
    labels=_ofr_episodes(),
)


OIL_STAGFLATION_CATALOG = EventCatalogVersion(
    scenario_code="oil_stagflation",
    version="world-bank-oil-stagflation-2022-v1",
    source_name="World Bank Global Economic Prospects",
    source_url=WORLD_BANK_STAGFLATION_URL,
    definition={
        "target": "historically_recognized_global_stagflation_episode",
        "method": "High global inflation combined with weak growth around the 1970s oil shocks.",
        "coverage_start": "1973-01-01",
        "coverage_end": "1983-12-31",
        "trigger_context": ["1973-74 oil shock", "1978-79 oil shock"],
        "label_precision": "year",
    },
    limitations=(
        "The World Bank states that stagflation has no precise universal numerical definition.",
        "Only one broad official historical episode is retained; probability must remain insufficient.",
        "Oil-shock triggers are context and are not counted as separate outcome events.",
    ),
    effective_from=EFFECTIVE_FROM,
    labels=(
        HistoricalEventLabel(
            code="global-oil-stagflation-1973-1982",
            started_at=datetime(1973, 1, 1, tzinfo=UTC),
            ended_at=datetime(1982, 12, 31, 23, 59, 59, tzinfo=UTC),
            start_precision="year",
            end_precision="year",
            region_code="GLOBAL",
            source_url=WORLD_BANK_STAGFLATION_URL,
            source_note="Calendar bounds encode the broad annual episode, not exact daily turning points.",
        ),
    ),
)


CRYPTO_UNWIND_CATALOG = EventCatalogVersion(
    scenario_code="crypto_leverage_unwind",
    version="official-source-gap-2026-v1",
    source_name="Bybit public market API documentation",
    source_url=BYBIT_MARKET_DOCS_URL,
    definition={
        "target": "crypto_leverage_unwind_onset",
        "method": "No official outcome catalog with a stable historical machine contract is available.",
        "coverage_start": None,
        "coverage_end": None,
    },
    limitations=(
        "Exchange market data are signals and do not define an official historical crisis label.",
        "No event labels are stored; calibrated probability must remain null.",
    ),
    effective_from=EFFECTIVE_FROM,
    labels=(),
)


CHINA_HARD_LANDING_CATALOG = EventCatalogVersion(
    scenario_code="china_hard_landing",
    version="official-source-gap-2026-v1",
    source_name="National Bureau of Statistics of China",
    source_url=CHINA_NBS_URL,
    definition={
        "target": "china_hard_landing_onset",
        "method": "No official hard-landing event definition or versioned event list is published.",
        "coverage_start": None,
        "coverage_end": None,
    },
    limitations=(
        "Official GDP observations do not by themselves constitute a hard-landing event catalog.",
        "No documented stable machine API for event labels was found; HTML is not scraped.",
        "No event labels are stored; calibrated probability must remain null.",
    ),
    effective_from=EFFECTIVE_FROM,
    labels=(),
)


OFFICIAL_EVENT_CATALOGS = (
    GLOBAL_RECESSION_CATALOG,
    FINANCIAL_STRESS_CATALOG,
    OIL_STAGFLATION_CATALOG,
    CRYPTO_UNWIND_CATALOG,
    CHINA_HARD_LANDING_CATALOG,
)


def bootstrap_official_event_catalogs(
    repository: CrisisRadarRepository,
) -> dict[str, int]:
    return {
        catalog.scenario_code: repository.register_event_catalog(catalog)
        for catalog in OFFICIAL_EVENT_CATALOGS
    }
