from __future__ import annotations

import asyncio
import csv
import hashlib
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


class NewYorkFedSourceError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class NewYorkFedClient:
    """Bounded client for the official New York Fed GSCPI vintage matrix."""

    source_code = "new_york_fed"
    endpoint = (
        "https://www.newyorkfed.org/medialibrary/research/interactives/"
        "data/gscpi/gscpi_interactive_data.csv"
    )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        attempts: int = 3,
        timeout_seconds: float = 30,
        max_response_bytes: int = 1_000_000,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if attempts < 1 or attempts > 5:
            raise ValueError("attempts must be between 1 and 5")
        if max_response_bytes < 100_000 or max_response_bytes > 2_000_000:
            raise ValueError("New York Fed response limit must be 100 KB to 2 MB")
        self._client = client
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.sleep = sleep

    async def fetch_gscpi(self) -> bytes:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    async with client.stream(
                        "GET",
                        self.endpoint,
                        headers={
                            "Accept": "text/csv",
                            "User-Agent": "TradingAssistant-CrisisRadar/10",
                        },
                        timeout=self.timeout_seconds,
                    ) as response:
                        if response.status_code == 200:
                            content_length = response.headers.get("Content-Length", "")
                            if content_length.isdigit() and int(content_length) > self.max_response_bytes:
                                raise NewYorkFedSourceError(
                                    "New York Fed GSCPI response exceeds configured size limit"
                                )
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > self.max_response_bytes:
                                    raise NewYorkFedSourceError(
                                        "New York Fed GSCPI response exceeds configured size limit"
                                    )
                            return bytes(content)
                        status_code = response.status_code
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise NewYorkFedSourceError(
                            "New York Fed GSCPI request failed after retries"
                        ) from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                retryable = status_code == 429 or status_code >= 500
                if not retryable or attempt == self.attempts:
                    raise NewYorkFedSourceError(
                        f"New York Fed GSCPI returned HTTP {status_code}"
                    )
                await self.sleep(min(2 ** (attempt - 1), 5))
        finally:
            if owns_client:
                await client.aclose()
        raise NewYorkFedSourceError("New York Fed GSCPI request failed")


@dataclass(frozen=True)
class GscpiMatrixContract:
    vintage_count: int
    observation_count: int
    latest_vintage: str
    latest_observation_at: datetime
    latest_value: Decimal
    non_missing_value_count: int
    content_hash: str


class NewYorkFedAdapter:
    source_code = "new_york_fed"
    indicator_code = "global_supply_chain_pressure"
    _MISSING = frozenset({"", "#N/A"})

    @staticmethod
    def _parse_vintage(value: str) -> datetime:
        try:
            return datetime.strptime(value, "%b-%y").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise SourcePayloadError("invalid GSCPI vintage header") from exc

    def _matrix(
        self, payload: bytes, *, fetched_at: datetime
    ) -> tuple[list[datetime], list[tuple[datetime, list[Decimal | None]]], str]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if len(payload) > 1_000_000:
            raise SourcePayloadError("GSCPI payload exceeds adapter size limit")
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SourcePayloadError("GSCPI CSV must be UTF-8") from exc
        if "\x00" in text:
            raise SourcePayloadError("GSCPI CSV contains a NUL byte")
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error as exc:
            raise SourcePayloadError("invalid GSCPI CSV") from exc
        if len(rows) < 3 or len(rows) > 500:
            raise SourcePayloadError("GSCPI CSV row count is outside the contract")
        header = rows[0]
        if len(header) < 2 or len(header) > 120 or header[0] != "Date":
            raise SourcePayloadError("invalid GSCPI CSV header")
        vintages = [self._parse_vintage(value) for value in header[1:]]
        if vintages != sorted(set(vintages)):
            raise SourcePayloadError("GSCPI vintage headers must be unique and ordered")
        fetched_month = fetched_at.astimezone(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        if vintages[-1] > fetched_month:
            raise SourcePayloadError("GSCPI latest vintage is in the future")

        parsed: list[tuple[datetime, list[Decimal | None]]] = []
        seen_dates: set[datetime] = set()
        for row in rows[1:]:
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != len(header):
                raise SourcePayloadError("GSCPI CSV row width does not match header")
            try:
                observed_at = datetime.strptime(row[0], "%d-%b-%Y").replace(
                    tzinfo=timezone.utc
                )
            except ValueError as exc:
                raise SourcePayloadError("invalid GSCPI observation date") from exc
            if observed_at in seen_dates:
                raise SourcePayloadError("duplicate GSCPI observation date")
            if observed_at > fetched_at:
                raise SourcePayloadError("GSCPI observation date is in the future")
            seen_dates.add(observed_at)
            values: list[Decimal | None] = []
            for vintage, raw in zip(vintages, row[1:], strict=True):
                raw = raw.strip()
                if raw in self._MISSING:
                    values.append(None)
                    continue
                if observed_at > vintage:
                    raise SourcePayloadError(
                        "GSCPI vintage contains an observation unavailable in that month"
                    )
                try:
                    value = Decimal(raw)
                except InvalidOperation as exc:
                    raise SourcePayloadError("invalid GSCPI numeric value") from exc
                if not value.is_finite():
                    raise SourcePayloadError("GSCPI values must be finite")
                values.append(value)
            parsed.append((observed_at, values))
        dates = [item[0] for item in parsed]
        if not parsed or dates != sorted(dates):
            raise SourcePayloadError("GSCPI observations must be non-empty and ordered")
        return vintages, parsed, hashlib.sha256(payload).hexdigest()

    def inspect_matrix(
        self, payload: bytes, *, fetched_at: datetime
    ) -> GscpiMatrixContract:
        vintages, rows, content_hash = self._matrix(payload, fetched_at=fetched_at)
        latest_column = len(vintages) - 1
        current = [
            (observed_at, values[latest_column])
            for observed_at, values in rows
            if values[latest_column] is not None
        ]
        if not current:
            raise SourcePayloadError("latest GSCPI vintage contains no observations")
        observed_at, value = current[-1]
        assert value is not None
        if observed_at > fetched_at:
            raise SourcePayloadError("latest GSCPI observation is in the future")
        non_missing = sum(
            value is not None for _, values in rows for value in values
        )
        return GscpiMatrixContract(
            vintage_count=len(vintages),
            observation_count=len(rows),
            latest_vintage=vintages[-1].strftime("%Y-%m"),
            latest_observation_at=observed_at,
            latest_value=value,
            non_missing_value_count=non_missing,
            content_hash=content_hash,
        )

    def normalize_latest(
        self, payload: bytes, *, fetched_at: datetime
    ) -> Observation:
        contract = self.inspect_matrix(payload, fetched_at=fetched_at)
        return Observation(
            indicator_code=self.indicator_code,
            source_code=self.source_code,
            value=contract.latest_value.quantize(Decimal("0.0001")),
            unit="standard_deviations",
            observed_at=contract.latest_observation_at,
            # The CSV provides a release month, not an exact publication time.
            # First collection time is the conservative availability boundary.
            released_at=fetched_at,
            fetched_at=fetched_at,
            vintage=contract.latest_vintage,
            quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
            content_hash=contract.content_hash,
        )
