from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.news_clients import GdeltDiscoveryClient, RssClient
from trading_bot.db import Database


async def validate() -> dict:
    root = Path(tempfile.mkdtemp(prefix="crisis-news-contract-"))
    service = CrisisRadarService(
        CrisisRadarRepository(Database(root / "contract.sqlite3")),
        feature_flags=CrisisRadarFeatureFlags(news_events_v2=True),
    )
    now = datetime.now(timezone.utc)
    results = {}
    required = ("fed_news", "ecb_news", "sec_news", "cftc_news", "bis_news", "boj_news", "rbi_news")
    for code in required:
        results[code] = await service.sync_news(RssClient(code), fetched_at=now)
    results["gdelt_discovery"] = await service.sync_gdelt_discovery(
        GdeltDiscoveryClient(), fetched_at=now
    )
    return {
        "checked_at": now.isoformat(),
        "temporary_database": str(root / "contract.sqlite3"),
        "results": results,
        "passed": all(
            results[code]["status"] == "succeeded"
            for code in required
        ),
        "optional_discovery_status": results["gdelt_discovery"]["status"],
    }


def main() -> None:
    result = asyncio.run(validate())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
