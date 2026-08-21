from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from trading_bot.crisis_radar.catalog import (  # noqa: E402
    FRED_V11_DEPTH_INDICATORS,
    FRED_V12_RESEARCH_INDICATORS,
    FRED_V19_RESEARCH_INDICATORS,
)
from trading_bot.crisis_radar.sources.base import SeriesRequest  # noqa: E402
from trading_bot.crisis_radar.sources.fred_client import FredClient  # noqa: E402


SERIES_IDS = tuple(
    item.provider_series_id
    for item in (
        FRED_V11_DEPTH_INDICATORS
        + FRED_V12_RESEARCH_INDICATORS
        + FRED_V19_RESEARCH_INDICATORS
    )
)


async def main() -> None:
    load_dotenv()
    client = FredClient(os.environ["FRED_API_KEY"])
    for series_id in SERIES_IDS:
        try:
            metadata_payload = await client.fetch_series_metadata(series_id)
            metadata_rows = json.loads(metadata_payload).get("seriess", [])
            metadata = metadata_rows[0] if metadata_rows else {}
            payload = await client.fetch(
                SeriesRequest(series_id, series_id, "raw"), limit=3
            )
            rows = [
                row
                for row in json.loads(payload).get("observations", [])
                if row.get("value") not in (None, ".")
            ]
            latest = rows[0]["date"] if rows else "empty"
            print(
                f"{series_id}\tOK\t{len(rows)}\t{latest}\t"
                f"{metadata.get('frequency_short', '?')}\t"
                f"{metadata.get('units_short', '?')}\t"
                f"{metadata.get('title', '?')}"
            )
        except Exception as exc:  # pragma: no cover - diagnostic CLI
            print(f"{series_id}\tERROR\t{type(exc).__name__}")


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Live-verify configured FRED Crisis Radar v11 contracts"
    ).parse_args()
    asyncio.run(main())
