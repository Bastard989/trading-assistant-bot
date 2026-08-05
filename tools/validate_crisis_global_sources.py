from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.global_clients import BisClient, OecdClient, WorldBankClient
from trading_bot.crisis_radar.sources.fred_client import FredClient
from trading_bot.db import Database


async def validate(output_dir: Path | None = None, *, with_fred: bool = False) -> dict:
    root = output_dir or Path(tempfile.mkdtemp(prefix="crisis-global-contract-"))
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "contract.sqlite3"
    service = CrisisRadarService(
        CrisisRadarRepository(Database(database_path)),
        feature_flags=CrisisRadarFeatureFlags(global_sources_v2=True),
    )
    now = datetime.now(timezone.utc)
    results = {
        "world_bank": await service.sync_world_bank(
            WorldBankClient(), fetched_at=now, recompute_after=False
        ),
        "bis": await service.sync_bis(BisClient(), fetched_at=now, recompute_after=False),
        "oecd": await service.sync_oecd(OecdClient(), fetched_at=now, recompute_after=False),
    }
    if with_fred:
        load_dotenv()
        api_key = os.getenv("FRED_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("FRED_API_KEY is required for --with-fred")
        results["fred"] = await service.sync_fred(
            FredClient(api_key), fetched_at=now, recompute_after=False
        )
    return {
        "checked_at": now.isoformat(),
        "temporary_database": str(database_path),
        "results": results,
        "passed": all(item["status"] == "succeeded" for item in results.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--with-fred", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(validate(args.output_dir, with_fred=args.with_fred))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
