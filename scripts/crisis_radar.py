from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from trading_bot.crisis_radar.catalog import (
    BYBIT_INDICATORS,
    BYBIT_RESEARCH_INDICATORS,
    FRED_INDICATORS,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.fred_client import FredClient
from trading_bot.crisis_radar.sources.bybit import BybitClient
from trading_bot.crisis_radar.sources.europe_clients import EcbClient, EurostatClient
from trading_bot.crisis_radar.sources.global_clients import BisClient, OecdClient, WorldBankClient
from trading_bot.crisis_radar.sources.official_clients import BeaClient, EiaClient
from trading_bot.crisis_radar.sources.news_clients import news_client_for
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
            for item in FRED_INDICATORS + BYBIT_INDICATORS + BYBIT_RESEARCH_INDICATORS
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
