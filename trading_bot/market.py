from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any

import httpx

from trading_bot.models import MarketTicker, Sentiment


class MarketError(RuntimeError):
    pass


class InvalidSymbolError(MarketError):
    pass


class MarketUnavailableError(MarketError):
    pass


class MarketClient:
    def __init__(
        self,
        market: str = "futures",
        *,
        client: httpx.AsyncClient | None = None,
        retries: int = 2,
        cache_ttl: float = 2,
        base_delay: float = 0.2,
    ) -> None:
        self.market = market
        if market == "spot":
            self.base_url = "https://api.binance.com"
            self.ticker_path = "/api/v3/ticker/24hr"
            self.price_path = "/api/v3/ticker/price"
        else:
            self.base_url = "https://fapi.binance.com"
            self.ticker_path = "/fapi/v1/ticker/24hr"
            self.price_path = "/fapi/v1/ticker/price"
        self._client = client
        self._owns_client = client is None
        self.retries = retries
        self.cache_ttl = cache_ttl
        self.base_delay = base_delay
        self._cache: dict[str, tuple[float, Any]] = {}
        self._failures = 0
        self._circuit_open_until = 0.0

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(connect=5, read=15, write=5, pool=5)
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        return self._client

    async def _request_json(self, path: str, *, params: dict[str, str | int] | None = None, cache: bool = False) -> Any:
        now = time.monotonic()
        if now < self._circuit_open_until:
            raise MarketUnavailableError("Binance circuit breaker is open")
        cache_key = f"{path}:{json.dumps(params or {}, sort_keys=True)}"
        cached = self._cache.get(cache_key)
        if cache and cached and now - cached[0] <= self.cache_ttl:
            return cached[1]

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self._http_client().get(path, params=params)
                if response.status_code == 400:
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    if payload.get("code") == -1121:
                        raise InvalidSymbolError("Symbol is not available on Binance")
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError("Retryable Binance response", request=response.request, response=response)
                response.raise_for_status()
                try:
                    data = response.json()
                except ValueError as exc:
                    raise MarketUnavailableError("Binance returned invalid JSON") from exc
                self._failures = 0
                self._circuit_open_until = 0
                if cache:
                    self._cache[cache_key] = (time.monotonic(), data)
                return data
            except InvalidSymbolError:
                raise
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, MarketUnavailableError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                retry_after = 0.0
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                    try:
                        retry_after = min(5.0, max(0.0, float(exc.response.headers.get("Retry-After", "0"))))
                    except ValueError:
                        retry_after = 0.0
                delay = max(retry_after, self.base_delay * (2 ** attempt) + random.uniform(0, self.base_delay))
                await asyncio.sleep(delay)
        self._failures += 1
        if self._failures >= 5:
            self._circuit_open_until = time.monotonic() + 30
        raise MarketUnavailableError("Binance market data is temporarily unavailable") from last_error

    async def get_price(self, symbol: str) -> float:
        symbol = normalize_symbol(symbol)
        data = await self._request_json(self.price_path, params={"symbol": symbol}, cache=True)
        return float(data["price"])

    async def get_prices(self, symbols: set[str]) -> dict[str, float]:
        normalized = {normalize_symbol(symbol) for symbol in symbols}
        data = await self._request_json(self.price_path, cache=True)
        prices = {item["symbol"]: float(item["price"]) for item in data}
        return {symbol: prices[symbol] for symbol in normalized if symbol in prices}

    async def get_tickers(self, symbols: set[str]) -> list[MarketTicker]:
        normalized = {normalize_symbol(symbol) for symbol in symbols if symbol.strip()}
        if not normalized:
            return []
        data = await self._request_json(self.ticker_path, cache=True)

        tickers = [parse_ticker(item) for item in data if item.get("symbol") in normalized]
        tickers.sort(key=lambda ticker: ticker.symbol)
        return tickers

    async def top_by_activity(self, limit: int = 10) -> list[MarketTicker]:
        data = await self._request_json(self.ticker_path, cache=True)

        tickers = [parse_ticker(item) for item in data if is_usdt_symbol(item.get("symbol", ""))]
        tickers = [
            ticker for ticker in tickers
            if ticker.quote_volume >= 100_000_000
            and ticker.price > 0
            and 1 <= ticker.intraday_range_percent <= 25
            and abs(ticker.price_change_percent) <= 40
        ]
        tickers.sort(key=lambda ticker: ticker.activity_score, reverse=True)
        return tickers[:limit]

    async def get_sentiment(self, symbol: str) -> Sentiment:
        symbol = normalize_symbol(symbol)
        if self.market != "futures":
            return Sentiment(symbol, None, None, None, "Long/short ratio is available for Binance futures only.")

        data: list[dict[str, Any]] = await self._request_json(
            "/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "5m", "limit": 1},
            cache=True,
        )

        if not data:
            return Sentiment(symbol, None, None, None, "Binance futures returned no sentiment data.")

        row = data[-1]
        long_percent = float(row["longAccount"]) * 100
        short_percent = float(row["shortAccount"]) * 100
        ratio = float(row["longShortRatio"])
        return Sentiment(symbol, long_percent, short_percent, ratio, "Binance futures global long/short accounts, 5m")

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 120,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, float]]:
        symbol = normalize_symbol(symbol)
        path = "/api/v3/klines" if self.market == "spot" else "/fapi/v1/klines"
        params: dict[str, str | int] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        data = await self._request_json(path, params=params, cache=True)
        return [
            {
                "open_time": float(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": float(row[6]),
            }
            for row in data
        ]

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip().lower()
    aliases = {
        "биткоин": "BTC", "биток": "BTC", "биточек": "BTC", "bitcoin": "BTC",
        "эфир": "ETH", "ефир": "ETH", "эфириум": "ETH", "ефириум": "ETH",
        "эфирка": "ETH", "ефирка": "ETH", "ethereum": "ETH",
        "солана": "SOL", "солянка": "SOL", "солик": "SOL", "соль": "SOL",
    }
    symbol = aliases.get(value, value).upper().replace("/", "").replace("-", "")
    if symbol and not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"
    return symbol


def is_usdt_symbol(symbol: str) -> bool:
    excluded = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
    return symbol.endswith("USDT") and not symbol.endswith(excluded)


def parse_ticker(item: dict[str, Any]) -> MarketTicker:
    return MarketTicker(
        symbol=item["symbol"],
        price=float(item.get("lastPrice") or item.get("price") or 0),
        quote_volume=float(item.get("quoteVolume") or 0),
        price_change_percent=float(item.get("priceChangePercent") or 0),
        high_price=float(item.get("highPrice") or 0),
        low_price=float(item.get("lowPrice") or 0),
    )
