from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date

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
        rows: list[dict] = []
        offset = 0
        while offset < max_rows:
            params = {
                "series_id": request.provider_series_id,
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
                "sort_order": "asc",
                "limit": str(min(page_size, max_rows - offset)),
                "offset": str(offset),
            }
            if initial_release:
                params.update(
                    {
                        "output_type": "4",
                        "realtime_start": observation_start.isoformat(),
                        "realtime_end": observation_end.isoformat(),
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
            if len(page) < min(page_size, max_rows - offset):
                break
            offset += len(page)
        if len(rows) >= max_rows:
            raise FredClientError("FRED history exceeds the configured row limit")
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
