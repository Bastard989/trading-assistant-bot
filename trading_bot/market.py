from __future__ import annotations

import asyncio
from typing import Any

import httpx

from trading_bot.models import MarketTicker, Sentiment


class MarketClient:
    def __init__(self, market: str = "futures") -> None:
        self.market = market
        if market == "spot":
            self.base_url = "https://api.binance.com"
            self.ticker_path = "/api/v3/ticker/24hr"
            self.price_path = "/api/v3/ticker/price"
        else:
            self.base_url = "https://fapi.binance.com"
            self.ticker_path = "/fapi/v1/ticker/24hr"
            self.price_path = "/fapi/v1/ticker/price"

    async def get_price(self, symbol: str) -> float:
        symbol = normalize_symbol(symbol)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=15) as client:
            response = await client.get(self.price_path, params={"symbol": symbol})
            response.raise_for_status()
            data = response.json()
        return float(data["price"])

    async def get_prices(self, symbols: set[str]) -> dict[str, float]:
        normalized = {normalize_symbol(symbol) for symbol in symbols}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20) as client:
            response = await client.get(self.price_path)
            response.raise_for_status()
            data = response.json()
        prices = {item["symbol"]: float(item["price"]) for item in data}
        return {symbol: prices[symbol] for symbol in normalized if symbol in prices}

    async def top_by_activity(self, limit: int = 10) -> list[MarketTicker]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20) as client:
            response = await client.get(self.ticker_path)
            response.raise_for_status()
            data = response.json()

        tickers = [parse_ticker(item) for item in data if is_usdt_symbol(item.get("symbol", ""))]
        tickers = [ticker for ticker in tickers if ticker.quote_volume > 0 and ticker.price > 0]
        tickers.sort(key=lambda ticker: ticker.activity_score, reverse=True)
        return tickers[:limit]

    async def get_sentiment(self, symbol: str) -> Sentiment:
        symbol = normalize_symbol(symbol)
        if self.market != "futures":
            return Sentiment(symbol, None, None, None, "Long/short ratio is available for Binance futures only.")

        url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params={"symbol": symbol, "period": "5m", "limit": 1})
            response.raise_for_status()
            data: list[dict[str, Any]] = response.json()

        if not data:
            return Sentiment(symbol, None, None, None, "Binance futures returned no sentiment data.")

        row = data[-1]
        long_percent = float(row["longAccount"]) * 100
        short_percent = float(row["shortAccount"]) * 100
        ratio = float(row["longShortRatio"])
        return Sentiment(symbol, long_percent, short_percent, ratio, "Binance futures global long/short accounts, 5m")

    async def close(self) -> None:
        await asyncio.sleep(0)


def normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper().replace("/", "").replace("-", "")
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
