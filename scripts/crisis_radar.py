from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from trading_bot.crisis_radar.catalog import (
    BYBIT_INDICATORS,
    BYBIT_RESEARCH_INDICATORS,
    FRED_GLOBAL_V2_INDICATORS,
    FRED_INDICATORS,
    FRED_V11_DEPTH_INDICATORS,
    FRED_V12_RESEARCH_INDICATORS,
    FRED_V19_RESEARCH_INDICATORS,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.fred_client import FredClient
from trading_bot.crisis_radar.sources.bybit import BybitClient
from trading_bot.crisis_radar.sources.europe_clients import EcbClient, EurostatClient
from trading_bot.crisis_radar.sources.global_clients import BisClient, OecdClient, WorldBankClient
from trading_bot.crisis_radar.sources.official_clients import BeaClient, EiaClient
from trading_bot.crisis_radar.sources.news_clients import news_client_for
from trading_bot.crisis_radar.sources.new_york_fed import NewYorkFedClient
from trading_bot.crisis_radar.sources.portwatch import PortWatchClient
from trading_bot.crisis_radar.sources.stablecoins import BinanceMarketClient
from trading_bot.db import CURRENT_SCHEMA_VERSION, Database


def _database_path(value: str | None) -> Path:
    return Path(value or os.getenv("DATABASE_PATH", "data/trading_bot.sqlite3")).expanduser()


def _require_current_schema(database: Database) -> None:
    try:
        with database.connect() as connection:
            version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
    except sqlite3.Error as exc:
        raise RuntimeError("Database is not initialized; run the migrate command first") from exc
    if version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema is {version}, expected {CURRENT_SCHEMA_VERSION}; run the migrate command first"
        )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Crisis Radar administration")
    parser.add_argument(
        "command",
        choices=(
            "migrate",
            "bootstrap",
            "sync",
            "backfill",
            "recompute",
            "status",
            "calendar",
            "news",
            "derive-labels",
        ),
    )
    parser.add_argument("--database", help="SQLite path; defaults to DATABASE_PATH")
    parser.add_argument("--locale", choices=("ru", "en"), default="ru")
    parser.add_argument(
        "--source",
        choices=(
            "all",
            "fred",
            "calendar",
            "bea",
            "eia",
            "ecb",
            "eurostat",
            "world_bank",
            "bis",
            "oecd",
            "oecd_labour_research",
            "imf_portwatch",
            "new_york_fed",
            "bybit_stablecoin_research",
            "binance_market",
            "news",
            "fed_news",
            "ecb_news",
            "sec_news",
            "cftc_news",
            "bis_news",
            "boj_news",
            "rbi_news",
            "boe_news",
            "boc_news",
            "fdic_news",
            "hkma_news",
            "nbs_news",
            "bok_news",
            "ofac_news",
            "bybit",
        ),
        default="all",
        help="Source to synchronize or backfill",
    )
    parser.add_argument("--days", type=int, choices=range(1, 91), default=30)
    parser.add_argument("--limit", type=int, choices=range(1, 51), default=20)
    parser.add_argument("--from", dest="started_on", default="1990-01-01")
    parser.add_argument("--through", dest="ended_on", default=date.today().isoformat())
    parser.add_argument(
        "--indicator",
        action="append",
        choices=tuple(
            item.code
            for item in (
                FRED_INDICATORS
                + FRED_GLOBAL_V2_INDICATORS
                + FRED_V11_DEPTH_INDICATORS
                + FRED_V12_RESEARCH_INDICATORS
                + FRED_V19_RESEARCH_INDICATORS
                + BYBIT_INDICATORS
                + BYBIT_RESEARCH_INDICATORS
            )
        ),
        help="Limit a FRED or Bybit backfill to one or more registered indicators",
    )
    args = parser.parse_args()
    path = _database_path(args.database)

    if args.command == "migrate":
        Database(path, auto_migrate=True)
        print(json.dumps({"ok": True, "database": str(path), "schema": CURRENT_SCHEMA_VERSION}))
        return

    database = Database(path, auto_migrate=False)
    _require_current_schema(database)
    service = CrisisRadarService(CrisisRadarRepository(database))
    if args.command == "bootstrap":
        print(json.dumps(service.bootstrap(), ensure_ascii=False))
    elif args.command == "sync":
        async def sync_configured_sources() -> dict:
            results = {}
            fred_key = os.getenv("FRED_API_KEY", "").strip()
            bea_key = os.getenv("BEA_API_KEY", "").strip()
            eia_key = os.getenv("EIA_API_KEY", "").strip()
            combined = args.source == "all"
            if args.source in {"all", "fred"} and fred_key:
                fred_client = FredClient(fred_key)
                results["fred"] = await service.sync_fred(
                    fred_client, recompute_after=not combined
                )
                results["fred_calendar"] = await service.sync_fred_calendar(fred_client)
            elif args.source == "calendar" and fred_key:
                results["fred_calendar"] = await service.sync_fred_calendar(
                    FredClient(fred_key)
                )
            if args.source in {"all", "bea"} and bea_key:
                results["bea"] = await service.sync_bea(
                    BeaClient(bea_key), recompute_after=not combined
                )
            if args.source in {"all", "eia"} and eia_key:
                results["eia"] = await service.sync_eia(
                    EiaClient(eia_key), recompute_after=not combined
                )
            if args.source in {"all", "ecb"}:
                results["ecb"] = await service.sync_ecb(EcbClient(), recompute_after=not combined)
            if args.source in {"all", "eurostat"}:
                results["eurostat"] = await service.sync_eurostat(
                    EurostatClient(), recompute_after=not combined
                )
            if args.source in {"all", "world_bank"}:
                results["world_bank"] = await service.sync_world_bank(
                    WorldBankClient(), recompute_after=not combined
                )
            if args.source in {"all", "bis"}:
                results["bis"] = await service.sync_bis(
                    BisClient(), recompute_after=not combined
                )
            if args.source in {"all", "oecd"}:
                results["oecd"] = await service.sync_oecd(
                    OecdClient(), recompute_after=not combined
                )
            if (
                args.source in {"all", "oecd_labour_research"}
                and service.feature_flags.scoring_v11
            ):
                results["oecd_labour_research"] = await service.sync_oecd_labour(
                    OecdClient(), recompute_after=False
                )
            elif args.source == "oecd_labour_research":
                raise RuntimeError(
                    "CRISIS_RADAR_SCORING_V11 must be enabled for the OECD labour research collector"
                )
            if (
                args.source in {"all", "imf_portwatch"}
                and service.feature_flags.scoring_v11
            ):
                results["imf_portwatch"] = await service.sync_portwatch(
                    PortWatchClient(), recompute_after=False
                )
            elif args.source == "imf_portwatch":
                raise RuntimeError(
                    "CRISIS_RADAR_SCORING_V11 must be enabled for the PortWatch research collector"
                )
            if args.source in {"all", "new_york_fed"} and service.feature_flags.scoring_v11:
                results["new_york_fed"] = await service.sync_new_york_fed(
                    NewYorkFedClient(), recompute_after=False
                )
            elif args.source == "new_york_fed":
                raise RuntimeError(
                    "CRISIS_RADAR_SCORING_V11 must be enabled for the GSCPI research collector"
                )
            if args.source in {"all", "binance_market"} and service.feature_flags.scoring_v11:
                results["binance_market"] = await service.sync_binance_stablecoin(
                    BinanceMarketClient(), recompute_after=False
                )
            elif args.source == "binance_market":
                raise RuntimeError(
                    "CRISIS_RADAR_SCORING_V11 must be enabled for the stablecoin research collector"
                )
            if (
                args.source in {"all", "bybit_stablecoin_research"}
                and service.feature_flags.scoring_v11
            ):
                results["bybit_stablecoin_research"] = (
                    await service.sync_bybit_stablecoin(
                        BybitClient(), recompute_after=False
                    )
                )
            elif args.source == "bybit_stablecoin_research":
                raise RuntimeError(
                    "CRISIS_RADAR_SCORING_V11 must be enabled for the stablecoin research collector"
                )
            news_source_codes = (
                "fed_news",
                "ecb_news",
                "sec_news",
                "cftc_news",
                "bis_news",
                "boj_news",
                "rbi_news",
                "boe_news",
                "boc_news",
                "fdic_news",
                "hkma_news",
                "nbs_news",
                "bok_news",
                "ofac_news",
            )
            for source_code in news_source_codes:
                if args.source in {"all", "news", source_code}:
                    results[source_code] = await service.sync_news(news_client_for(source_code))
            if args.source in {"all", "bybit"}:
                results["bybit"] = await service.sync_bybit(
                    BybitClient(), recompute_after=not combined
                )
            if not results:
                raise RuntimeError(f"API key is not configured for source: {args.source}")
            if combined:
                overview = service.recompute()
                results["market"] = {"stage": None if overview is None else overview.stage.value}
            return results

        print(json.dumps(asyncio.run(sync_configured_sources()), ensure_ascii=False))
    elif args.command == "backfill":
        try:
            started_on = date.fromisoformat(args.started_on)
            ended_on = date.fromisoformat(args.ended_on)
        except ValueError as exc:
            raise RuntimeError("--from and --through must be ISO dates") from exc
        selected = None if args.indicator is None else set(args.indicator)
        if args.source == "fred":
            fred_key = os.getenv("FRED_API_KEY", "").strip()
            if not fred_key:
                raise RuntimeError("FRED_API_KEY is required for the FRED backfill")
            operation = service.backfill_fred(
                FredClient(fred_key),
                started_on=started_on,
                ended_on=ended_on,
                indicator_codes=selected,
            )
        elif args.source == "bybit":
            operation = service.backfill_bybit(
                BybitClient(),
                started_on=started_on,
                ended_on=ended_on,
                indicator_codes=selected,
            )
        else:
            raise RuntimeError("bounded backfill supports --source fred or --source bybit")
        print(
            json.dumps(
                asyncio.run(operation),
                ensure_ascii=False,
            )
        )
    elif args.command == "derive-labels":
        print(json.dumps(service.derive_crypto_event_catalog(), ensure_ascii=False, indent=2))
    elif args.command == "recompute":
        overview = service.recompute()
        print(
            json.dumps(
                {"stage": None if overview is None else overview.stage.value},
                ensure_ascii=False,
            )
        )
    elif args.command == "status":
        print(json.dumps(service.overview(locale=args.locale), ensure_ascii=False, indent=2))
    elif args.command == "calendar":
        print(
            json.dumps(
                service.calendar(locale=args.locale, days=args.days), ensure_ascii=False, indent=2
            )
        )
    else:
        print(
            json.dumps(
                service.news(locale=args.locale, days=args.days, limit=args.limit),
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
