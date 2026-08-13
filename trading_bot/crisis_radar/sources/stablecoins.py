from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


class BinanceMarketSourceError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class BinanceMarketClient:
    """Bounded public-market client using Binance's documented data-only host."""

    source_code = "binance_market"
    endpoint = "https://data-api.binance.vision/api/v3/ticker/bookTicker"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        timeout_seconds: float = 15,
        max_response_bytes: int = 64_000,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if attempts < 1 or attempts > 5:
            raise ValueError("attempts must be between 1 and 5")
        if max_response_bytes < 1_000 or max_response_bytes > 256_000:
            raise ValueError("Binance response limit must be 1 KB to 256 KB")
        self._client = client
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.sleep = sleep

    async def fetch_usdc_usdt_book(self) -> bytes:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    async with client.stream(
                        "GET",
                        self.endpoint,
                        params={"symbol": "USDCUSDT"},
                        headers={
                            "Accept": "application/json",
                            "User-Agent": "TradingAssistant-CrisisRadar/11",
                        },
                        timeout=self.timeout_seconds,
                    ) as response:
                        if response.status_code == 200:
                            content_length = response.headers.get("Content-Length", "")
                            if content_length.isdigit() and int(content_length) > self.max_response_bytes:
                                raise BinanceMarketSourceError(
                                    "Binance response exceeds configured size limit"
                                )
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > self.max_response_bytes:
                                    raise BinanceMarketSourceError(
                                        "Binance response exceeds configured size limit"
                                    )
                            return bytes(content)
                        status_code = response.status_code
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise BinanceMarketSourceError(
                            "Binance market-data request failed after retries"
                        ) from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                retryable = status_code == 429 or status_code >= 500
                if not retryable or attempt == self.attempts:
                    raise BinanceMarketSourceError(
                        f"Binance market data returned HTTP {status_code}"
                    )
                await self.sleep(min(2 ** (attempt - 1), 5))
        finally:
            if owns_client:
                await client.aclose()
        raise BinanceMarketSourceError("Binance market-data request failed")


class StablecoinDislocationAdapter:
    """Normalize one-venue quotes without treating venues as independent risks."""

    @staticmethod
    def _stress_distance(*, bid: Decimal, ask: Decimal) -> Decimal:
        if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask <= 0:
            raise SourcePayloadError("stablecoin quotes must be finite and positive")
        if bid > ask:
            raise SourcePayloadError("stablecoin bid exceeds ask")
        if ask > Decimal("100"):
            raise SourcePayloadError("stablecoin quote is outside the safety contract")
        midpoint = (bid + ask) / Decimal("2")
        half_spread = (ask - bid) / Decimal("2")
        return max(abs(midpoint - Decimal("1")), half_spread) * Decimal("100")

    @staticmethod
    def _observation(
        *,
        indicator_code: str,
        source_code: str,
        bid: Decimal,
        ask: Decimal,
        fetched_at: datetime,
        content_hash: str,
    ) -> Observation:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        value = StablecoinDislocationAdapter._stress_distance(bid=bid, ask=ask)
        return Observation(
            indicator_code=indicator_code,
            source_code=source_code,
            value=value.quantize(Decimal("0.0001")),
            unit="percent_from_peg",
            observed_at=fetched_at,
            released_at=fetched_at,
            fetched_at=fetched_at,
            vintage=fetched_at.astimezone(timezone.utc).isoformat(),
            quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
            content_hash=content_hash,
        )

    def normalize_binance(self, payload: bytes, *, fetched_at: datetime) -> Observation:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if len(payload) > 64_000:
            raise SourcePayloadError("Binance stablecoin payload exceeds size limit")
        try:
            document = json.loads(payload)
            if not isinstance(document, dict) or document.get("symbol") != "USDCUSDT":
                raise SourcePayloadError("invalid Binance stablecoin symbol")
            bid = Decimal(str(document["bidPrice"]))
            ask = Decimal(str(document["askPrice"]))
            bid_quantity = Decimal(str(document["bidQty"]))
            ask_quantity = Decimal(str(document["askQty"]))
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            KeyError,
            TypeError,
            InvalidOperation,
        ) as exc:
            raise SourcePayloadError("invalid Binance stablecoin payload") from exc
        if (
            not bid_quantity.is_finite()
            or not ask_quantity.is_finite()
            or bid_quantity <= 0
            or ask_quantity <= 0
        ):
            raise SourcePayloadError("Binance stablecoin quote has no executable size")
        return self._observation(
            indicator_code="usdc_usdt_dislocation_binance",
            source_code="binance_market",
            bid=bid,
            ask=ask,
            fetched_at=fetched_at,
            content_hash=hashlib.sha256(payload).hexdigest(),
        )

    def normalize_bybit(self, payload: bytes, *, fetched_at: datetime) -> Observation:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if len(payload) > 4_000_000:
            raise SourcePayloadError("Bybit stablecoin payload exceeds size limit")
        try:
            document = json.loads(payload)
            if not isinstance(document, dict) or document.get("retCode") != 0:
                raise SourcePayloadError("Bybit returned a stablecoin API error")
            result = document["result"]
            rows = result["list"]
            if result.get("category") != "spot" or not isinstance(rows, list) or len(rows) != 1:
                raise SourcePayloadError("invalid Bybit stablecoin result")
            row = rows[0]
            if not isinstance(row, dict) or row.get("symbol") != "USDCUSDT":
                raise SourcePayloadError("invalid Bybit stablecoin symbol")
            provider_time = datetime.fromtimestamp(
                int(document["time"]) / 1000, timezone.utc
            )
            bid = Decimal(str(row["bid1Price"]))
            ask = Decimal(str(row["ask1Price"]))
            bid_quantity = Decimal(str(row["bid1Size"]))
            ask_quantity = Decimal(str(row["ask1Size"]))
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            KeyError,
            TypeError,
            ValueError,
            InvalidOperation,
        ) as exc:
            raise SourcePayloadError("invalid Bybit stablecoin payload") from exc
        if abs((provider_time - fetched_at).total_seconds()) > 300:
            raise SourcePayloadError(
                "Bybit stablecoin provider time is outside the freshness contract"
            )
        if (
            not bid_quantity.is_finite()
            or not ask_quantity.is_finite()
            or bid_quantity <= 0
            or ask_quantity <= 0
        ):
            raise SourcePayloadError("Bybit stablecoin quote has no executable size")
        return self._observation(
            indicator_code="usdc_usdt_dislocation_bybit",
            source_code="bybit",
            bid=bid,
            ask=ask,
            fetched_at=fetched_at,
            content_hash=hashlib.sha256(payload).hexdigest(),
        )
