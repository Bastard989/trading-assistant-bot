from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from trading_bot.crisis_radar.sources.base import SourcePayloadError


@dataclass(frozen=True)
class FredReleaseEvent:
    provider_event_id: str
    release_name: str
    release_date: date
    region_code: str
    importance: str
    source_url: str
    fetched_at: datetime
    payload: dict


_HIGH_IMPORTANCE = {
    "consumer price index",
    "employment situation",
    "gross domestic product",
    "federal open market committee",
    "personal income and outlays",
}
_MEDIUM_IMPORTANCE = {
    "producer price index",
    "advance monthly sales for retail and food services",
    "g.17 industrial production and capacity utilization",
    "unemployment insurance weekly claims report",
}


class FredCalendarAdapter:
    def normalize(
        self,
        payload: bytes,
        *,
        fetched_at: datetime,
        start_date: date,
        end_date: date,
    ) -> list[FredReleaseEvent]:
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourcePayloadError("FRED calendar returned invalid JSON") from exc
        rows = document.get("release_dates") if isinstance(document, dict) else None
        if not isinstance(rows, list):
            raise SourcePayloadError("FRED calendar payload does not contain release_dates")

        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("release_name", "")).strip()
            lowered = name.casefold()
            importance = self._importance(lowered)
            if not importance:
                continue
            try:
                release_date = date.fromisoformat(str(row.get("date", "")))
                release_id = str(int(row["release_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not start_date <= release_date <= end_date:
                continue
            result.append(
                FredReleaseEvent(
                    provider_event_id=release_id,
                    release_name=name,
                    release_date=release_date,
                    region_code="US",
                    importance=importance,
                    source_url=f"https://fred.stlouisfed.org/release?rid={release_id}",
                    fetched_at=fetched_at,
                    payload={
                        "release_last_updated": row.get("release_last_updated"),
                        "source": "fred/releases/dates",
                    },
                )
            )
        return sorted(result, key=lambda item: (item.release_date, item.release_name))

    @staticmethod
    def _importance(lowered_name: str) -> str:
        if lowered_name in _HIGH_IMPORTANCE:
            return "high"
        if lowered_name in _MEDIUM_IMPORTANCE:
            return "medium"
        return ""
