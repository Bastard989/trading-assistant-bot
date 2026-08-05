from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx


class NewsSourceError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class RssClient:
    _FEEDS = {
        "fed_news": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "ecb_news": "https://www.ecb.europa.eu/rss/press.html",
        "sec_news": "https://www.sec.gov/news/pressreleases.rss",
        "cftc_news": "https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
        "bis_news": "https://www.bis.org/doclist/all_pressrels.rss",
        "boj_news": "https://www.boj.or.jp/en/rss/whatsnew.xml",
        "rbi_news": "https://rbi.org.in/pressreleases_rss.xml",
    }

    def __init__(
        self,
        source_code: str,
        *,
        client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        timeout_seconds: float = 25,
        sleep: Sleep = asyncio.sleep,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        if source_code not in self._FEEDS:
            raise ValueError("unsupported official RSS source")
        if attempts < 1 or attempts > 5:
            raise ValueError("attempts must be between 1 and 5")
        self.source_code = source_code
        self._client = client
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.max_response_bytes = max_response_bytes

    async def fetch(self) -> bytes:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    response = await client.get(
                        self._FEEDS[self.source_code],
                        headers={
                            "Accept": "application/rss+xml, application/xml, text/xml",
                            "User-Agent": "TradingAssistant-CrisisRadar/5",
                        },
                    )
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise NewsSourceError("official RSS request failed after retries") from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                if response.status_code == 200:
                    if len(response.content) > self.max_response_bytes:
                        raise NewsSourceError("official RSS response exceeds configured size limit")
                    return response.content
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self.attempts:
                    raise NewsSourceError(f"official RSS returned HTTP {response.status_code}")
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
        raise NewsSourceError("official RSS request failed")


class GdeltDiscoveryClient:
    source_code = "gdelt_discovery"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
        max_response_bytes: int = 3_000_000,
    ) -> None:
        self._client = client
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    async def fetch(self, *, timespan: str = "1h") -> bytes:
        if timespan not in {"15min", "1h", "6h", "24h"}:
            raise ValueError("unsupported GDELT discovery timespan")
        query = (
            '(bankruptcy OR default OR "bank run" OR "emergency liquidity" OR sanctions '
            'OR war OR "supply disruption" OR "oil shock" OR "trading suspension" '
            'OR "exchange hack" OR "stablecoin depeg" OR recession)'
        )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            try:
                response = await client.get(
                    "https://api.gdeltproject.org/api/v2/doc/doc",
                    params={
                        "query": query,
                        "mode": "ArtList",
                        "format": "json",
                        "maxrecords": "250",
                        "timespan": timespan,
                        "sort": "DateDesc",
                    },
                    headers={"Accept": "application/json", "User-Agent": "TradingAssistant-CrisisRadar/6"},
                )
            except httpx.RequestError as exc:
                raise NewsSourceError("GDELT discovery request failed") from exc
            if response.status_code != 200:
                raise NewsSourceError(f"GDELT discovery returned HTTP {response.status_code}")
            if len(response.content) > self.max_response_bytes:
                raise NewsSourceError("GDELT discovery response exceeds configured size limit")
            return response.content
        finally:
            if owns_client:
                await client.aclose()
