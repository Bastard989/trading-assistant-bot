from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

import httpx

from trading_bot.crisis_radar.sources.base import SeriesRequest


class FredClientError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class FredClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.stlouisfed.org/fred",
        client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        timeout_seconds: float = 15,
        sleep: Sleep = asyncio.sleep,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        if not api_key.strip():
            raise ValueError("FRED_API_KEY is required for FRED synchronization")
        if attempts < 1 or attempts > 5:
            raise ValueError("attempts must be between 1 and 5")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self._client = client
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.max_response_bytes = max_response_bytes

    async def fetch(self, request: SeriesRequest, *, limit: int = 24) -> bytes:
        if limit < 1 or limit > 1000:
            raise ValueError("FRED observation limit must be between 1 and 1000")
        return await self._get(
            "/series/observations",
            {
                "series_id": request.provider_series_id,
                "sort_order": "desc",
                "limit": str(limit),
            },
        )

    async def fetch_series_metadata(self, series_id: str) -> bytes:
        """Return the provider contract for one FRED series."""

        if not series_id.strip():
            raise ValueError("FRED series_id is required")
        return await self._get("/series", {"series_id": series_id.strip()})

    async def fetch_vintage_dates(
        self,
        series_id: str,
        *,
        realtime_start: date,
        realtime_end: date,
        limit: int = 10_000,
    ) -> bytes:
        """Return dates when a series changed in ALFRED's real-time archive."""

        if not series_id.strip():
            raise ValueError("FRED series_id is required")
        if realtime_end < realtime_start:
            raise ValueError("FRED vintage end date must not precede start date")
        if limit < 1 or limit > 10_000:
            raise ValueError("FRED vintage limit must be between 1 and 10000")
        return await self._get(
            "/series/vintagedates",
            {
                "series_id": series_id.strip(),
                "realtime_start": realtime_start.isoformat(),
                "realtime_end": realtime_end.isoformat(),
                "sort_order": "asc",
                "limit": str(limit),
            },
        )

    async def fetch_initial_release_probe(
        self,
        request: SeriesRequest,
        *,
        observation_start: date,
        observation_end: date,
    ) -> bytes:
        """Probe whether FRED can return bounded initial-release observations.

        This deliberately requests one row only.  It is a capability check, not
        a historical backfill, and therefore cannot be used as replay evidence.
        The four-year bound stays below FRED's 2,000-vintage JSON limit.
        """

        if observation_end < observation_start:
            raise ValueError("FRED probe end date must not precede start date")
        if (observation_end - observation_start).days > 1460:
            raise ValueError("FRED initial-release probe must not exceed four years")
        return await self._get(
            "/series/observations",
            {
                "series_id": request.provider_series_id,
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
                "realtime_start": observation_start.isoformat(),
                "realtime_end": observation_end.isoformat(),
                "output_type": "4",
                "sort_order": "desc",
                "limit": "1",
            },
        )

    async def fetch_history(
        self,
        request: SeriesRequest,
        *,
        observation_start: date,
        observation_end: date,
        page_size: int = 1000,
        max_rows: int = 20_000,
        initial_release: bool = False,
    ) -> bytes:
        if observation_end < observation_start:
            raise ValueError("FRED history end date must not precede start date")
        if page_size < 100 or page_size > 1000:
            raise ValueError("FRED history page_size must be between 100 and 1000")
        if max_rows < 1 or max_rows > 20_000:
            raise ValueError("FRED history max_rows must be between 1 and 20000")
        realtime_ranges: list[tuple[date | None, date | None]] = [(None, None)]
        if initial_release:
            # A number of series were added to ALFRED years after their first
            # observation.  FRED rejects an output_type=4 real-time window that
            # ends before the first available vintage instead of returning an
            # empty page.  Discover the first vintage and never issue those
            # impossible early requests.
            vintage_payload = await self.fetch_vintage_dates(
                request.provider_series_id,
                realtime_start=observation_start,
                realtime_end=observation_end,
                limit=1,
            )
            try:
                vintage_document = json.loads(vintage_payload)
                vintage_dates = vintage_document["vintage_dates"]
                if not isinstance(vintage_dates, list):
                    raise TypeError
                first_vintage = (
                    date.fromisoformat(str(vintage_dates[0])) if vintage_dates else None
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise FredClientError("FRED vintage history returned malformed JSON") from exc
            if first_vintage is None:
                return b'{"observations":[]}'
            # FRED JSON limits one request to 2,000 vintage dates. Four-year
            # release windows stay below that ceiling even for daily series.
            realtime_ranges = []
            chunk_start = max(observation_start, first_vintage)
            while chunk_start <= observation_end:
                chunk_end = min(observation_end, chunk_start + timedelta(days=1460))
                realtime_ranges.append((chunk_start, chunk_end))
                chunk_start = chunk_end + timedelta(days=1)

        rows: list[dict] = []
        for realtime_start, realtime_end in realtime_ranges:
            offset = 0
            while len(rows) < max_rows:
                limit = min(page_size, max_rows - len(rows))
                params = {
                    "series_id": request.provider_series_id,
                    "observation_start": observation_start.isoformat(),
                    "observation_end": observation_end.isoformat(),
                    "sort_order": "asc",
                    "limit": str(limit),
                    "offset": str(offset),
                }
                if realtime_start is not None and realtime_end is not None:
                    params.update(
                        {
                            "output_type": "4",
                            "realtime_start": realtime_start.isoformat(),
                            "realtime_end": realtime_end.isoformat(),
                        }
                    )
                payload = await self._get(
                    "/series/observations",
                    params,
                )
                try:
                    document = json.loads(payload)
                    page = document["observations"]
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise FredClientError("FRED history returned malformed JSON") from exc
                if not isinstance(page, list):
                    raise FredClientError("FRED history observations must be a list")
                rows.extend(page)
                if len(page) < limit:
                    break
                offset += len(page)
        if len(rows) >= max_rows:
            raise FredClientError("FRED history exceeds the configured row limit")
        rows.sort(
            key=lambda item: (
                str(item.get("date", "")),
                str(item.get("realtime_start", "")),
                str(item.get("value", "")),
            )
        )
        encoded = json.dumps({"observations": rows}, separators=(",", ":")).encode()
        if len(encoded) > 12_000_000:
            raise FredClientError("combined FRED history exceeds the configured size limit")
        return encoded

    async def fetch_release_dates(self, *, start_date: date, end_date: date) -> bytes:
        if end_date < start_date:
            raise ValueError("FRED release calendar end_date must not precede start_date")
        return await self._get(
            "/releases/dates",
            {
                "realtime_start": start_date.isoformat(),
                "realtime_end": end_date.isoformat(),
                "include_release_dates_with_no_data": "true",
                "order_by": "release_date",
                "sort_order": "asc",
                "limit": "1000",
            },
        )

    async def _get(self, path: str, params: dict[str, str]) -> bytes:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        request_params = {**params, "api_key": self.api_key, "file_type": "json"}
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    response = await client.get(
                        f"{self.base_url}{path}",
                        params=request_params,
                        headers={"Accept": "application/json", "User-Agent": "TradingAssistant-CrisisRadar/1"},
                    )
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise FredClientError("FRED request failed after retries") from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                if response.status_code == 200:
                    if len(response.content) > self.max_response_bytes:
                        raise FredClientError("FRED response exceeds the configured size limit")
                    return response.content
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self.attempts:
                    raise FredClientError(f"FRED returned HTTP {response.status_code}")
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2 ** (attempt - 1)
                await self.sleep(min(delay, 5))
        finally:
            if owns_client:
                await client.aclose()
        raise FredClientError("FRED request failed")
