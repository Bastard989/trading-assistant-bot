from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from trading_bot.crisis_radar.domain import Observation


class SourcePayloadError(ValueError):
    pass


@dataclass(frozen=True)
class SeriesRequest:
    indicator_code: str
    provider_series_id: str
    unit: str


class SourceAdapter(Protocol):
    source_code: str

    def normalize(
        self,
        payload: bytes,
        request: SeriesRequest,
        *,
        fetched_at: datetime,
    ) -> list[Observation]: ...
