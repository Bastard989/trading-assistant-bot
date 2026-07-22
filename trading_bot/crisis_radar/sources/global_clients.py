from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx


class GlobalSourceError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class _PublicClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        timeout_seconds: float = 35,
        sleep: Sleep = asyncio.sleep,
        max_response_bytes: int = 12_000_000,
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
                        headers={
                            "Accept": accept,
                            "User-Agent": "TradingAssistant-CrisisRadar/4",
                        },
                    )
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise GlobalSourceError(
                            "global public API request failed after retries"
                        ) from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                if response.status_code == 200:
                    if len(response.content) > self.max_response_bytes:
                        raise GlobalSourceError(
                            "global API response exceeds configured size limit"
                        )
                    return response.content
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self.attempts:
                    raise GlobalSourceError(
                        f"global public API returned HTTP {response.status_code}"
                    )
                retry_after = response.headers.get("Retry-After", "")
                delay = (
                    float(retry_after)
                    if retry_after.replace(".", "", 1).isdigit()
                    else 2 ** (attempt - 1)
                )
                await self.sleep(min(delay, 5))
        finally:
            if owns_client:
                await client.aclose()
        raise GlobalSourceError("global public API request failed")


class WorldBankClient(_PublicClient):
    async def fetch_gdp_growth(self, country: str, *, as_of: datetime) -> bytes:
        country_code = country.strip().upper()
        if country_code not in {"CHN", "WLD"}:
            raise ValueError("unsupported World Bank country code")
        return await self._get(
            f"https://api.worldbank.org/v2/country/{country_code}/indicator/NY.GDP.MKTP.KD.ZG",
            params={
                "format": "json",
                "date": f"{as_of.year - 7}:{as_of.year}",
                "per_page": "100",
            },
            accept="application/json",
        )


class BisClient(_PublicClient):
    async def fetch_credit_gaps(self) -> bytes:
        return await self._get(
            "https://data.bis.org/static/bulk/WS_CREDIT_GAP_csv_flat.zip",
            params={},
            accept="application/zip",
        )


class OecdClient(_PublicClient):
    async def fetch_composite_leading_indicators(self, *, as_of: datetime) -> bytes:
        start_year = as_of.year - 3
        return await self._get(
            (
                "https://sdmx.oecd.org/public/rest/v1/data/"
                "OECD.SDD.STES,DSD_STES@DF_CLI,4.1/"
                "G20+CHN.M.LI...AA...H"
            ),
            params={
                "startPeriod": f"{start_year}-01",
                "endPeriod": f"{as_of.year}-{as_of.month:02d}",
                "dimensionAtObservation": "AllDimensions",
            },
            accept="text/csv",
        )
