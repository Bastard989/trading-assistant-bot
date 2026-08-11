from __future__ import annotations

import asyncio
import hashlib
import json
from bisect import bisect_right
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


class BybitSourceError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class BybitClient:
    base_url = "https://api.bybit.com/v5/market"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        timeout_seconds: float = 20,
        sleep: Sleep = asyncio.sleep,
        max_response_bytes: int = 4_000_000,
        max_history_pages: int = 100,
        max_history_rows: int = 20_000,
        max_history_bytes: int = 12_000_000,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be positive")
        if min(max_response_bytes, max_history_pages, max_history_rows, max_history_bytes) < 1:
            raise ValueError("Bybit safety limits must be positive")
        self._client = client
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.max_response_bytes = max_response_bytes
        self.max_history_pages = max_history_pages
        self.max_history_rows = max_history_rows
        self.max_history_bytes = max_history_bytes

    async def _get(self, path: str, params: dict[str, str]) -> bytes:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    response = await client.get(
                        f"{self.base_url}/{path}",
                        params=params,
                        headers={"Accept": "application/json", "User-Agent": "TradingAssistant-CrisisRadar/4"},
                    )
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise BybitSourceError("Bybit request failed after retries") from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                if response.status_code == 200:
                    if len(response.content) > self.max_response_bytes:
                        raise BybitSourceError("Bybit response exceeds configured size limit")
                    return response.content
                if response.status_code != 429 and response.status_code < 500:
                    raise BybitSourceError(f"Bybit returned HTTP {response.status_code}")
                if attempt == self.attempts:
                    raise BybitSourceError(f"Bybit returned HTTP {response.status_code}")
                await self.sleep(min(2 ** (attempt - 1), 5))
        finally:
            if owns_client:
                await client.aclose()
        raise BybitSourceError("Bybit request failed")

    async def fetch_funding(self, symbol: str) -> bytes:
        return await self._get(
            "funding/history",
            {"category": "linear", "symbol": symbol, "limit": "30"},
        )

    async def fetch_open_interest(self, symbol: str) -> bytes:
        return await self._get(
            "open-interest",
            {"category": "linear", "symbol": symbol, "intervalTime": "1d", "limit": "30"},
        )

    async def fetch_daily_klines(self, symbol: str) -> bytes:
        return await self._get(
            "kline",
            {"category": "linear", "symbol": symbol, "interval": "D", "limit": "45"},
        )

    async def fetch_option_tickers(self, base_coin: str = "BTC") -> bytes:
        cleaned = base_coin.strip().upper()
        if cleaned not in {"BTC", "ETH"}:
            raise ValueError("Bybit option base coin must be BTC or ETH")
        return await self._get(
            "tickers",
            {"category": "option", "baseCoin": cleaned},
        )

    @staticmethod
    def _page(payload: bytes, *, symbol: str) -> tuple[dict, list]:
        try:
            document = json.loads(payload)
            if document.get("retCode") != 0:
                raise BybitSourceError("Bybit returned an API error")
            result = document["result"]
            rows = result["list"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BybitSourceError("Bybit returned an invalid response") from exc
        if not isinstance(result, dict) or not isinstance(rows, list):
            raise BybitSourceError("Bybit returned an invalid response")
        if result.get("symbol") not in {None, symbol}:
            raise BybitSourceError("Bybit response symbol mismatch")
        return result, rows

    def _check_history_budget(
        self, *, pages: int, rows: int, response_bytes: int
    ) -> None:
        if pages > self.max_history_pages:
            raise BybitSourceError("Bybit history exceeds configured page limit")
        if rows > self.max_history_rows:
            raise BybitSourceError("Bybit history exceeds configured row limit")
        if response_bytes > self.max_history_bytes:
            raise BybitSourceError("Bybit history exceeds configured size limit")

    @staticmethod
    def _combined_payload(symbol: str, rows: list, *, cursor: str = "") -> bytes:
        return json.dumps(
            {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "symbol": symbol,
                    "category": "linear",
                    "list": rows,
                    "nextPageCursor": cursor,
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    async def _fetch_reverse_time_history(
        self,
        path: str,
        *,
        symbol: str,
        started_at: datetime,
        ended_at: datetime,
        time_parameter_names: tuple[str, str],
        limit: int,
        timestamp_at: Callable[[object], int],
        extra_params: dict[str, str],
    ) -> bytes:
        """Page reverse-chronological Bybit endpoints by moving the inclusive end time."""
        start_ms = int(started_at.timestamp() * 1000)
        end_ms = int(ended_at.timestamp() * 1000)
        if end_ms < start_ms:
            raise ValueError("Bybit history end must not precede start")
        start_name, end_name = time_parameter_names
        current_end = end_ms
        pages = rows_seen = response_bytes = 0
        rows_by_timestamp: dict[int, object] = {}
        seen_ends: set[int] = set()
        while current_end >= start_ms:
            if pages >= self.max_history_pages:
                raise BybitSourceError("Bybit history exceeds configured page limit")
            if current_end in seen_ends:
                raise BybitSourceError("Bybit history pagination loop detected")
            seen_ends.add(current_end)
            payload = await self._get(
                path,
                {
                    "category": "linear",
                    "symbol": symbol,
                    **extra_params,
                    start_name: str(start_ms),
                    end_name: str(current_end),
                    "limit": str(limit),
                },
            )
            pages += 1
            response_bytes += len(payload)
            _, page_rows = self._page(payload, symbol=symbol)
            rows_seen += len(page_rows)
            self._check_history_budget(
                pages=pages, rows=rows_seen, response_bytes=response_bytes
            )
            if not page_rows:
                break
            try:
                timestamps = [timestamp_at(row) for row in page_rows]
            except (IndexError, KeyError, TypeError, ValueError) as exc:
                raise BybitSourceError("Bybit returned an invalid history row") from exc
            for timestamp, row in zip(timestamps, page_rows, strict=True):
                if start_ms <= timestamp <= end_ms:
                    previous = rows_by_timestamp.get(timestamp)
                    if previous is not None and previous != row:
                        raise BybitSourceError("Bybit returned conflicting history rows")
                    rows_by_timestamp[timestamp] = row
            oldest = min(timestamps)
            if len(page_rows) < limit or oldest <= start_ms:
                break
            next_end = oldest - 1
            if next_end >= current_end:
                raise BybitSourceError("Bybit history pagination made no progress")
            current_end = next_end
        ordered = [rows_by_timestamp[item] for item in sorted(rows_by_timestamp, reverse=True)]
        return self._combined_payload(symbol, ordered)

    async def fetch_funding_history(
        self, symbol: str, *, started_at: datetime, ended_at: datetime
    ) -> bytes:
        """Fetch funding settlements using documented startTime/endTime reverse paging."""
        return await self._fetch_reverse_time_history(
            "funding/history",
            symbol=symbol,
            started_at=started_at,
            ended_at=ended_at,
            time_parameter_names=("startTime", "endTime"),
            limit=200,
            timestamp_at=lambda row: int(row["fundingRateTimestamp"]),
            extra_params={},
        )

    async def fetch_kline_history(
        self, symbol: str, *, started_at: datetime, ended_at: datetime
    ) -> bytes:
        """Fetch daily candles using documented start/end reverse paging."""
        return await self._fetch_reverse_time_history(
            "kline",
            symbol=symbol,
            started_at=started_at,
            ended_at=ended_at,
            time_parameter_names=("start", "end"),
            limit=1000,
            timestamp_at=lambda row: int(row[0]),
            extra_params={"interval": "D"},
        )

    async def fetch_open_interest_history(
        self, symbol: str, *, started_at: datetime, ended_at: datetime
    ) -> bytes:
        """Fetch daily OI using Bybit's documented nextPageCursor contract."""
        start_ms = int(started_at.timestamp() * 1000)
        end_ms = int(ended_at.timestamp() * 1000)
        if end_ms < start_ms:
            raise ValueError("Bybit history end must not precede start")
        cursor = ""
        seen_cursors: set[str] = set()
        pages = rows_seen = response_bytes = 0
        rows_by_timestamp: dict[int, object] = {}
        while True:
            if pages >= self.max_history_pages:
                raise BybitSourceError("Bybit history exceeds configured page limit")
            params = {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": "1d",
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "limit": "200",
            }
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("open-interest", params)
            pages += 1
            response_bytes += len(payload)
            result, page_rows = self._page(payload, symbol=symbol)
            rows_seen += len(page_rows)
            self._check_history_budget(
                pages=pages, rows=rows_seen, response_bytes=response_bytes
            )
            for row in page_rows:
                try:
                    timestamp = int(row["timestamp"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise BybitSourceError("Bybit returned an invalid history row") from exc
                if start_ms <= timestamp <= end_ms:
                    previous = rows_by_timestamp.get(timestamp)
                    if previous is not None and previous != row:
                        raise BybitSourceError("Bybit returned conflicting history rows")
                    rows_by_timestamp[timestamp] = row
            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise BybitSourceError("Bybit history pagination loop detected")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        ordered = [rows_by_timestamp[item] for item in sorted(rows_by_timestamp, reverse=True)]
        return self._combined_payload(symbol, ordered)


class BybitAdapter:
    source_code = "bybit"

    @staticmethod
    def _document(payload: bytes, symbol: str) -> tuple[dict, str]:
        try:
            document = json.loads(payload)
            if document.get("retCode") != 0:
                raise SourcePayloadError("Bybit returned an API error")
            result = document["result"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourcePayloadError("invalid Bybit response payload") from exc
        if result.get("symbol") not in {None, symbol}:
            raise SourcePayloadError("Bybit response symbol mismatch")
        return result, hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _observation(
        *,
        code: str,
        value: Decimal,
        observed_at: datetime,
        fetched_at: datetime,
        content_hash: str,
        unit: str = "percent",
        released_at: datetime | None = None,
    ) -> Observation:
        release = released_at or observed_at
        if observed_at > fetched_at or release > fetched_at:
            raise SourcePayloadError("Bybit observation is dated in the future")
        return Observation(
            indicator_code=code,
            source_code="bybit",
            value=value.quantize(Decimal("0.0001")),
            unit=unit,
            observed_at=observed_at,
            released_at=release,
            fetched_at=fetched_at,
            vintage=f"{fetched_at.date().isoformat()}:{content_hash[:12]}",
            quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
            content_hash=content_hash,
        )

    def normalize_funding(self, payload: bytes, *, symbol: str, fetched_at: datetime) -> list[Observation]:
        result, content_hash = self._document(payload, symbol)
        prefix = symbol.removesuffix("USDT").lower()
        observations = []
        for row in result.get("list", []):
            try:
                if not isinstance(row, dict):
                    raise SourcePayloadError("invalid Bybit funding row")
                if row.get("symbol") not in {None, symbol}:
                    raise SourcePayloadError("Bybit response symbol mismatch")
                value = Decimal(str(row["fundingRate"])) * 100
                observed_at = datetime.fromtimestamp(int(row["fundingRateTimestamp"]) / 1000, timezone.utc)
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid Bybit funding row") from exc
            if not value.is_finite():
                raise SourcePayloadError("Bybit funding must be finite")
            observations.append(
                self._observation(
                    code=f"{prefix}_funding_rate",
                    value=value,
                    observed_at=observed_at,
                    fetched_at=fetched_at,
                    content_hash=content_hash,
                )
            )
        if not observations:
            raise SourcePayloadError("Bybit funding response is empty")
        return sorted(observations, key=lambda item: item.observed_at)

    def normalize_oi_change(self, payload: bytes, *, symbol: str, fetched_at: datetime) -> list[Observation]:
        result, content_hash = self._document(payload, symbol)
        prefix = symbol.removesuffix("USDT").lower()
        values: dict[datetime, Decimal] = {}
        for row in result.get("list", []):
            try:
                observed_at = datetime.fromtimestamp(int(row["timestamp"]) / 1000, timezone.utc)
                value = Decimal(str(row["openInterest"]))
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid Bybit open-interest row") from exc
            if not value.is_finite() or value <= 0:
                raise SourcePayloadError("Bybit open interest must be finite and positive")
            values[observed_at] = value
        dates = sorted(values)
        observations = []
        for observed_at in dates:
            index = bisect_right(dates, observed_at - timedelta(days=7)) - 1
            if index < 0:
                continue
            base = values[dates[index]]
            change = abs((values[observed_at] / base - 1) * 100)
            observations.append(
                self._observation(
                    code=f"{prefix}_oi_7d_abs_change",
                    value=change,
                    observed_at=observed_at,
                    fetched_at=fetched_at,
                    content_hash=content_hash,
                )
            )
        if not observations:
            raise SourcePayloadError("Bybit open-interest response has insufficient history")
        return observations

    def normalize_signed_oi_changes(
        self,
        payload: bytes,
        *,
        symbol: str,
        fetched_at: datetime,
    ) -> list[Observation]:
        """Calculate signed 1/7/30-day changes without using future OI values."""
        result, content_hash = self._document(payload, symbol)
        prefix = symbol.removesuffix("USDT").lower()
        values: dict[datetime, Decimal] = {}
        for row in result.get("list", []):
            try:
                observed_at = datetime.fromtimestamp(int(row["timestamp"]) / 1000, timezone.utc)
                value = Decimal(str(row["openInterest"]))
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid Bybit open-interest row") from exc
            if not value.is_finite() or value <= 0:
                raise SourcePayloadError("Bybit open interest must be finite and positive")
            values[observed_at] = value
        dates = sorted(values)
        observations: list[Observation] = []
        for observed_at in dates:
            for days in (1, 7, 30):
                prior_index = bisect_right(dates, observed_at - timedelta(days=days)) - 1
                if prior_index < 0:
                    continue
                change = (values[observed_at] / values[dates[prior_index]] - 1) * 100
                observations.append(
                    self._observation(
                        code=f"{prefix}_oi_{days}d_change",
                        value=change,
                        observed_at=observed_at,
                        fetched_at=fetched_at,
                        content_hash=content_hash,
                    )
                )
        if not observations:
            raise SourcePayloadError("Bybit signed open-interest response has insufficient history")
        return observations
    def normalize_drawdown(self, payload: bytes, *, symbol: str, fetched_at: datetime) -> list[Observation]:
        result, content_hash = self._document(payload, symbol)
        prefix = symbol.removesuffix("USDT").lower()
        closes: dict[datetime, Decimal] = {}
        for row in result.get("list", []):
            try:
                observed_at = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc)
                close = Decimal(str(row[4]))
            except (InvalidOperation, IndexError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid Bybit kline row") from exc
            if not close.is_finite() or close <= 0:
                raise SourcePayloadError("Bybit close must be finite and positive")
            if observed_at + timedelta(days=1) <= fetched_at:
                closes[observed_at] = close
        dates = sorted(closes)
        observations = []
        for observed_at in dates:
            window_start = bisect_right(dates, observed_at - timedelta(days=30))
            window = dates[max(0, window_start - 1) : bisect_right(dates, observed_at)]
            if len(window) < 2:
                continue
            peak = max(closes[item] for item in window)
            drawdown = (closes[observed_at] / peak - 1) * 100
            observations.append(
                self._observation(
                    code=f"{prefix}_30d_drawdown",
                    value=drawdown,
                    observed_at=observed_at,
                    fetched_at=fetched_at,
                    content_hash=content_hash,
                    released_at=observed_at + timedelta(days=1),
                )
            )
        if not observations:
            raise SourcePayloadError("Bybit kline response has insufficient history")
        return observations

    def normalize_daily_funding_history(
        self,
        payload: bytes,
        *,
        symbol: str,
        fetched_at: datetime,
        started_at: datetime,
        ended_at: datetime,
    ) -> list[Observation]:
        """Keep the final settled funding observation from each UTC day."""
        observations = self.normalize_funding(payload, symbol=symbol, fetched_at=fetched_at)
        by_day: dict[date, Observation] = {}
        for observation in observations:
            if not started_at <= observation.observed_at <= ended_at:
                continue
            day = observation.observed_at.date()
            previous = by_day.get(day)
            if previous is None or observation.observed_at > previous.observed_at:
                by_day[day] = observation
        normalized = sorted(by_day.values(), key=lambda item: item.observed_at)
        if not normalized:
            raise SourcePayloadError("Bybit funding history is empty in the requested window")
        return normalized

    def normalize_oi_change_history(
        self,
        payload: bytes,
        *,
        symbol: str,
        fetched_at: datetime,
        started_at: datetime,
        ended_at: datetime,
    ) -> list[Observation]:
        """Calculate each 7-day change only from OI values available at that timestamp."""
        observations = self.normalize_oi_change(payload, symbol=symbol, fetched_at=fetched_at)
        normalized = [
            item for item in observations if started_at <= item.observed_at <= ended_at
        ]
        if not normalized:
            raise SourcePayloadError("Bybit open-interest history is empty in the requested window")
        return normalized

    def normalize_drawdown_history(
        self,
        payload: bytes,
        *,
        symbol: str,
        fetched_at: datetime,
        started_at: datetime,
        ended_at: datetime,
    ) -> list[Observation]:
        """Calculate rolling drawdown from current and earlier closed daily candles only."""
        observations = self.normalize_drawdown(payload, symbol=symbol, fetched_at=fetched_at)
        normalized = [
            item
            for item in observations
            if started_at <= item.observed_at <= ended_at
            and item.observed_at + timedelta(days=1) <= fetched_at
        ]
        if not normalized:
            raise SourcePayloadError("Bybit kline history is empty in the requested window")
        return normalized

    def normalize_oi_research_history(
        self,
        payload: bytes,
        *,
        symbol: str,
        fetched_at: datetime,
        started_at: datetime,
        ended_at: datetime,
    ) -> list[Observation]:
        """Store immutable OI levels and signed 7-day changes for label research."""
        result, content_hash = self._document(payload, symbol)
        prefix = symbol.removesuffix("USDT").lower()
        levels: dict[datetime, Decimal] = {}
        for row in result.get("list", []):
            try:
                observed_at = datetime.fromtimestamp(int(row["timestamp"]) / 1000, timezone.utc)
                value = Decimal(str(row["openInterest"]))
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid Bybit open-interest row") from exc
            if not value.is_finite() or value <= 0:
                raise SourcePayloadError("Bybit open interest must be finite and positive")
            levels[observed_at] = value
        dates = sorted(levels)
        observations: list[Observation] = []
        for observed_at in dates:
            if not started_at <= observed_at <= ended_at:
                continue
            observations.append(
                self._observation(
                    code=f"{prefix}_open_interest",
                    value=levels[observed_at],
                    observed_at=observed_at,
                    fetched_at=fetched_at,
                    content_hash=content_hash,
                    unit="coin",
                )
            )
            prior_index = bisect_right(dates, observed_at - timedelta(days=7)) - 1
            if prior_index >= 0:
                signed_change = (levels[observed_at] / levels[dates[prior_index]] - 1) * 100
                observations.append(
                    self._observation(
                        code=f"{prefix}_oi_7d_change",
                        value=signed_change,
                        observed_at=observed_at,
                        fetched_at=fetched_at,
                        content_hash=content_hash,
                    )
                )
        if not observations:
            raise SourcePayloadError("Bybit open-interest research history is empty")
        return observations

    def normalize_price_research_history(
        self,
        payload: bytes,
        *,
        symbol: str,
        fetched_at: datetime,
        started_at: datetime,
        ended_at: datetime,
    ) -> list[Observation]:
        """Store completed daily closes and signed returns without future candles."""
        result, content_hash = self._document(payload, symbol)
        prefix = symbol.removesuffix("USDT").lower()
        closes: dict[datetime, Decimal] = {}
        for row in result.get("list", []):
            try:
                observed_at = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc)
                close = Decimal(str(row[4]))
            except (InvalidOperation, IndexError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid Bybit kline row") from exc
            if not close.is_finite() or close <= 0:
                raise SourcePayloadError("Bybit close must be finite and positive")
            if observed_at + timedelta(days=1) <= fetched_at:
                closes[observed_at] = close
        dates = sorted(closes)
        observations: list[Observation] = []
        for observed_at in dates:
            if not started_at <= observed_at <= ended_at:
                continue
            released_at = observed_at + timedelta(days=1)
            observations.append(
                self._observation(
                    code=f"{prefix}_close_price",
                    value=closes[observed_at],
                    observed_at=observed_at,
                    released_at=released_at,
                    fetched_at=fetched_at,
                    content_hash=content_hash,
                    unit="USDT",
                )
            )
            prior_index = bisect_right(dates, observed_at - timedelta(days=7)) - 1
            if prior_index >= 0:
                signed_return = (closes[observed_at] / closes[dates[prior_index]] - 1) * 100
                observations.append(
                    self._observation(
                        code=f"{prefix}_return_7d",
                        value=signed_return,
                        observed_at=observed_at,
                        released_at=released_at,
                        fetched_at=fetched_at,
                        content_hash=content_hash,
                    )
                )
        if not observations:
            raise SourcePayloadError("Bybit price research history is empty")
        return observations


def classify_signed_oi_state(
    *,
    oi_change: Decimal,
    price_change: Decimal,
    funding_rate: Decimal | None = None,
) -> str:
    """Explain leverage build/unwind; it is analytical and never creates an order."""
    if not oi_change.is_finite() or not price_change.is_finite():
        raise ValueError("OI and price changes must be finite")
    if funding_rate is not None and not funding_rate.is_finite():
        raise ValueError("funding rate must be finite")
    if oi_change <= Decimal("-25") and price_change <= Decimal("-10"):
        return "liquidation_unwind"
    if oi_change < Decimal("-5"):
        return "orderly_deleveraging"
    if oi_change >= Decimal("25"):
        if price_change < 0 or (funding_rate is not None and funding_rate < 0):
            return "leverage_build_short"
        return "leverage_build_long"
    if price_change < 0 and oi_change > 0:
        return "price_down_oi_up"
    if price_change < 0 and oi_change <= 0:
        return "price_down_oi_down"
    if price_change >= 0 and oi_change > 0:
        return "price_up_oi_up"
    return "price_up_oi_down"
