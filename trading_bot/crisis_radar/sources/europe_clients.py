from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import httpx


class EuropeSourceError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class _EuropeClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        timeout_seconds: float = 25,
        sleep: Sleep = asyncio.sleep,
        max_response_bytes: int = 4_000_000,
    ) -> None:
        if attempts < 1 or attempts > 5:
            raise ValueError("attempts must be between 1 and 5")
        self._client = client
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.max_response_bytes = max_response_bytes

    async def _get(self, url: str, *, params: dict[str, str], accept: str) -> bytes:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    response = await client.get(
                        url,
                        params=params,
                        headers={"Accept": accept, "User-Agent": "TradingAssistant-CrisisRadar/3"},
                    )
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise EuropeSourceError("European public API request failed after retries") from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                if response.status_code == 200:
                    if len(response.content) > self.max_response_bytes:
                        raise EuropeSourceError("European API response exceeds configured size limit")
                    return response.content
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self.attempts:
                    raise EuropeSourceError(f"European public API returned HTTP {response.status_code}")
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2 ** (attempt - 1)
                await self.sleep(min(delay, 5))
        finally:
            if owns_client:
                await client.aclose()
        raise EuropeSourceError("European public API request failed")


class EcbClient(_EuropeClient):
    async def fetch_ciss(self, *, as_of: datetime) -> bytes:
        start = (as_of.date() - timedelta(days=550)).isoformat()
        return await self._get(
            "https://data-api.ecb.europa.eu/service/data/CISS/D.U2.Z0Z.4F.EC.SS_CIN.IDX",
            params={"startPeriod": start, "format": "csvdata"},
            accept="text/csv",
        )


class EurostatClient(_EuropeClient):
    async def fetch_real_gdp(self, *, as_of: datetime) -> bytes:
        return await self._get(
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/namq_10_gdp",
            params={
                "lang": "en",
                "freq": "Q",
                "unit": "CLV_PCH_PRE",
                "na_item": "B1GQ",
                "s_adj": "SCA",
                "geo": "EA20",
                "sinceTimePeriod": f"{as_of.year - 7}-Q1",
            },
            accept="application/json",
        )
