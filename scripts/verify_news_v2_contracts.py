from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_bot.crisis_radar.news import normalize_official_news  # noqa: E402
from trading_bot.crisis_radar.sources.news_clients import news_client_for  # noqa: E402


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
    "hkma_news",
    "nbs_news",
    "ofac_news",
)


async def main() -> None:
    now = datetime.now(timezone.utc)
    for source_code in SOURCES:
        try:
            payload = await news_client_for(source_code).fetch()
            items = normalize_official_news(source_code, payload, fetched_at=now)
            latest = items[-1]
            print(
                f"{source_code}\tOK\t{len(items)}\t"
                f"{latest.published_at.isoformat()}\t{latest.url}"
            )
        except Exception as exc:  # pragma: no cover - diagnostic CLI
            print(f"{source_code}\tERROR\t{type(exc).__name__}\t{exc}")


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Live-verify configured official Crisis Radar news contracts"
    ).parse_args()
    asyncio.run(main())
