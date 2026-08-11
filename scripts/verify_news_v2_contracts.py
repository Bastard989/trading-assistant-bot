from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from trading_bot.crisis_radar.news import RssAdapter
from trading_bot.crisis_radar.sources.news_clients import RssClient


SOURCES = (
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
)


async def main() -> None:
    now = datetime.now(timezone.utc)
    for source_code in SOURCES:
        try:
            payload = await RssClient(source_code).fetch()
            items = RssAdapter(source_code).normalize(payload, fetched_at=now)
            latest = items[-1]
            print(
                f"{source_code}\tOK\t{len(items)}\t"
                f"{latest.published_at.isoformat()}\t{latest.url}"
            )
        except Exception as exc:  # pragma: no cover - diagnostic CLI
            print(f"{source_code}\tERROR\t{type(exc).__name__}\t{exc}")


if __name__ == "__main__":
    asyncio.run(main())
