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

from trading_bot.crisis_radar.sources.base import SeriesRequest  # noqa: E402
from trading_bot.crisis_radar.sources.fred_client import FredClient  # noqa: E402


SERIES_IDS = (
    "CCSA",
    "PAYEMS",
    "JTSJOL",
    "JTSQUR",
    "TEMPHELPS",
    "AWHMAN",
    "BAMLC0A0CM",
    "DPSACBW027SBOG",
    "WLCFLPCL",
    "PERMIT",
    "STLFSI4",
    "DFII10",
    "DTWEXBGS",
)


async def main() -> None:
    load_dotenv()
    client = FredClient(os.environ["FRED_API_KEY"])
    for series_id in SERIES_IDS:
        try:
            metadata_payload = await client._get(  # noqa: SLF001 - diagnostic contract check
                "/series", {"series_id": series_id}
            )
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
