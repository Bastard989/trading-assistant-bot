from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx


class OfficialSourceError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class _JsonApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        timeout_seconds: float = 20,
        sleep: Sleep = asyncio.sleep,
        max_response_bytes: int = 4_000_000,
    ) -> None:
        if not api_key.strip():
            raise ValueError("API key is required")
        if attempts < 1 or attempts > 5:
            raise ValueError("attempts must be between 1 and 5")
        self.api_key = api_key.strip()
        self._client = client
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.max_response_bytes = max_response_bytes

    async def _get(self, url: str, params: dict[str, str]) -> bytes:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    response = await client.get(
                        url,
                        params=params,
                        headers={"Accept": "application/json", "User-Agent": "TradingAssistant-CrisisRadar/2"},
                    )
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise OfficialSourceError("official API request failed after retries") from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                if response.status_code == 200:
                    if len(response.content) > self.max_response_bytes:
                        raise OfficialSourceError("official API response exceeds configured size limit")
                    return response.content
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self.attempts:
                    raise OfficialSourceError(f"official API returned HTTP {response.status_code}")
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2 ** (attempt - 1)
                await self.sleep(min(delay, 5))
        finally:
            if owns_client:
                await client.aclose()
        raise OfficialSourceError("official API request failed")


class BeaClient(_JsonApiClient):
    async def fetch_real_gdp(self, *, as_of: datetime) -> bytes:
        years = ",".join(str(year) for year in range(as_of.year - 7, as_of.year + 1))
        return await self._get(
            "https://apps.bea.gov/api/data",
            {
                "UserID": self.api_key,
                "method": "GetData",
                "datasetname": "NIPA",
                "TableName": "T10101",
                "Frequency": "Q",
                "Year": years,
                "ResultFormat": "JSON",
            },
        )


class EiaClient(_JsonApiClient):
    async def fetch_wti(self, *, start_date: str) -> bytes:
        return await self._get(
            "https://api.eia.gov/v2/petroleum/pri/spt/data/",
            {
                "api_key": self.api_key,
                "frequency": "daily",
                "data[0]": "value",
                "facets[series][]": "RWTC",
                "start": start_date,
                "sort[0][column]": "period",
                "sort[0][direction]": "asc",
                "length": "5000",
            },
        )
