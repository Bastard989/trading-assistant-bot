from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from statistics import median

import httpx

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


PORTWATCH_ITEM_ID = "3da2b9ca97684916b75c4013f95d18ab"
PORTWATCH_LAYER_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0"
)
PORTWATCH_QUERY_URL = f"{PORTWATCH_LAYER_URL}/query"
PORTWATCH_LOOKBACK_DAYS = 420
PORTWATCH_BASELINE_DAYS = 365
PORTWATCH_CURRENT_DAYS = 7
PORTWATCH_MAX_RESPONSE_BYTES = 512_000
PORTWATCH_MAX_ROWS = 500


@dataclass(frozen=True)
class PortWatchChokepoint:
    port_id: str
    port_name: str
    slug: str
    name_ru: str

    @property
    def indicator_code(self) -> str:
        return f"{self.slug}_transit_shortfall"

    @property
    def group_code(self) -> str:
        return f"{self.slug}_shipping"


PORTWATCH_CHOKEPOINTS = (
    PortWatchChokepoint("chokepoint1", "Suez Canal", "suez", "Суэцкий канал"),
    PortWatchChokepoint("chokepoint2", "Panama Canal", "panama", "Панамский канал"),
    PortWatchChokepoint(
        "chokepoint4", "Bab el-Mandeb Strait", "bab_el_mandeb", "Баб-эль-Мандебский пролив"
    ),
    PortWatchChokepoint("chokepoint5", "Malacca Strait", "malacca", "Малаккский пролив"),
    PortWatchChokepoint("chokepoint6", "Strait of Hormuz", "hormuz", "Ормузский пролив"),
)
_CHOKEPOINT_BY_ID = {item.port_id: item for item in PORTWATCH_CHOKEPOINTS}


class PortWatchSourceError(RuntimeError):
    pass


Sleep = Callable[[float], Awaitable[None]]


class PortWatchClient:
    """Bounded client for the public IMF PortWatch ArcGIS feature layer."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        attempts: int = 2,
        timeout_seconds: float = 15,
        sleep: Sleep = asyncio.sleep,
        max_response_bytes: int = PORTWATCH_MAX_RESPONSE_BYTES,
    ) -> None:
        if attempts < 1 or attempts > 5:
            raise ValueError("attempts must be between 1 and 5")
        if timeout_seconds <= 0 or timeout_seconds > 20:
            raise ValueError("PortWatch timeout must be between 0 and 20 seconds")
        if max_response_bytes < 1 or max_response_bytes > PORTWATCH_MAX_RESPONSE_BYTES:
            raise ValueError("invalid PortWatch response-size limit")
        self._client = client
        self.attempts = attempts
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.max_response_bytes = max_response_bytes

    async def fetch_chokepoint(self, port_id: str, *, as_of: datetime) -> bytes:
        if port_id not in _CHOKEPOINT_BY_ID:
            raise ValueError("unsupported PortWatch chokepoint")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        ended_on = as_of.astimezone(timezone.utc).date()
        started_on = ended_on - timedelta(days=PORTWATCH_LOOKBACK_DAYS)
        params = {
            "where": (
                f"portid='{port_id}' AND date >= DATE '{started_on.isoformat()}' "
                f"AND date <= DATE '{ended_on.isoformat()}'"
            ),
            "outFields": "date,portid,portname,n_total",
            "returnGeometry": "false",
            "orderByFields": "date ASC",
            "resultRecordCount": str(PORTWATCH_MAX_ROWS),
            "f": "json",
        }
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            for attempt in range(1, self.attempts + 1):
                try:
                    response = await client.get(
                        PORTWATCH_QUERY_URL,
                        params=params,
                        headers={
                            "Accept": "application/json",
                            "User-Agent": "TradingAssistant-CrisisRadar/4",
                        },
                    )
                except httpx.RequestError as exc:
                    if attempt == self.attempts:
                        raise PortWatchSourceError(
                            "PortWatch request failed after retries"
                        ) from exc
                    await self.sleep(min(2 ** (attempt - 1), 5))
                    continue
                if response.status_code == 200:
                    if len(response.content) > self.max_response_bytes:
                        raise PortWatchSourceError(
                            "PortWatch response exceeds configured size limit"
                        )
                    return response.content
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == self.attempts:
                    raise PortWatchSourceError(
                        f"PortWatch returned HTTP {response.status_code}"
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
        raise PortWatchSourceError("PortWatch request failed")


class PortWatchAdapter:
    """Calculate a causal seven-day transit shortfall from one current vintage.

    The latest seven complete daily transit counts are compared with the median
    of the immediately preceding 365 days.  The current window is excluded from
    the baseline.  Historical rows in a newly fetched ArcGIS payload can be
    revised, so only the latest transformed point is stored and it is explicitly
    marked with an estimated release time. The historical panel itself is not
    backfilled as causal observations; each stored transformed value is a new
    point-in-time vintage from its actual collection timestamp onward.
    """

    source_code = "imf_portwatch"
    _TOP_LEVEL_REQUIRED = {
        "objectIdFieldName",
        "uniqueIdField",
        "globalIdFieldName",
        "fields",
        "features",
    }
    _TOP_LEVEL_ALLOWED = _TOP_LEVEL_REQUIRED | {"exceededTransferLimit"}
    _FIELD_TYPES = {
        "date": "esriFieldTypeDateOnly",
        "portid": "esriFieldTypeString",
        "portname": "esriFieldTypeString",
        "n_total": "esriFieldTypeInteger",
    }

    def normalize_latest(
        self,
        payload: bytes,
        *,
        port_id: str,
        fetched_at: datetime,
    ) -> Observation:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if port_id not in _CHOKEPOINT_BY_ID:
            raise ValueError("unsupported PortWatch chokepoint")
        if len(payload) > PORTWATCH_MAX_RESPONSE_BYTES:
            raise SourcePayloadError("PortWatch payload exceeds adapter size limit")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourcePayloadError("invalid PortWatch JSON") from exc
        if not isinstance(document, dict) or "error" in document:
            raise SourcePayloadError("PortWatch returned an error payload")
        keys = set(document)
        if not self._TOP_LEVEL_REQUIRED.issubset(keys) or not keys.issubset(
            self._TOP_LEVEL_ALLOWED
        ):
            raise SourcePayloadError("unexpected PortWatch response schema")
        if document.get("exceededTransferLimit") is True:
            raise SourcePayloadError("PortWatch response was truncated")
        fields = document.get("fields")
        if not isinstance(fields, list) or len(fields) != len(self._FIELD_TYPES):
            raise SourcePayloadError("unexpected PortWatch field contract")
        try:
            field_contract = {item["name"]: item["type"] for item in fields}
        except (KeyError, TypeError) as exc:
            raise SourcePayloadError("invalid PortWatch field metadata") from exc
        if field_contract != self._FIELD_TYPES:
            raise SourcePayloadError("unexpected PortWatch field contract")
        features = document.get("features")
        required_rows = PORTWATCH_BASELINE_DAYS + PORTWATCH_CURRENT_DAYS
        if (
            not isinstance(features, list)
            or len(features) < required_rows
            or len(features) > PORTWATCH_MAX_ROWS
        ):
            raise SourcePayloadError("PortWatch row count is outside the contract")

        spec = _CHOKEPOINT_BY_ID[port_id]
        rows: list[tuple[datetime, Decimal]] = []
        seen_dates: set[datetime] = set()
        for feature in features:
            if not isinstance(feature, dict) or set(feature) != {"attributes"}:
                raise SourcePayloadError("invalid PortWatch feature")
            attributes = feature.get("attributes")
            if not isinstance(attributes, dict) or set(attributes) != set(
                self._FIELD_TYPES
            ):
                raise SourcePayloadError("unexpected PortWatch attributes")
            if (
                attributes.get("portid") != spec.port_id
                or attributes.get("portname") != spec.port_name
            ):
                raise SourcePayloadError("unexpected PortWatch chokepoint identity")
            try:
                observed_date = datetime.strptime(attributes["date"], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except (TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid PortWatch date") from exc
            if observed_date > fetched_at.astimezone(timezone.utc):
                raise SourcePayloadError("PortWatch observation is in the future")
            if observed_date in seen_dates:
                raise SourcePayloadError("duplicate PortWatch observation")
            count = attributes.get("n_total")
            if isinstance(count, bool) or not isinstance(count, int):
                raise SourcePayloadError("invalid PortWatch transit count")
            if count < 0 or count > 10_000:
                raise SourcePayloadError("PortWatch transit count is outside valid range")
            seen_dates.add(observed_date)
            rows.append((observed_date, Decimal(count)))
        if rows != sorted(rows, key=lambda item: item[0]):
            raise SourcePayloadError("PortWatch observations are not ordered")
        panel = rows[-required_rows:]
        if any(
            current[0] - previous[0] != timedelta(days=1)
            for previous, current in zip(panel, panel[1:])
        ):
            raise SourcePayloadError("PortWatch series lacks contiguous daily coverage")

        baseline_values = [value for _, value in panel[:PORTWATCH_BASELINE_DAYS]]
        current_values = [value for _, value in panel[PORTWATCH_BASELINE_DAYS:]]
        baseline = median(baseline_values)
        if baseline <= 0:
            raise SourcePayloadError("PortWatch baseline must be positive")
        current = sum(current_values, Decimal("0")) / Decimal(PORTWATCH_CURRENT_DAYS)
        shortfall = max(Decimal("0"), (Decimal("1") - current / baseline) * Decimal("100"))
        shortfall = shortfall.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        content_hash = hashlib.sha256(payload).hexdigest()
        return Observation(
            indicator_code=spec.indicator_code,
            source_code=self.source_code,
            value=shortfall,
            unit="percent_shortfall",
            observed_at=panel[-1][0],
            released_at=fetched_at,
            fetched_at=fetched_at,
            vintage=f"{fetched_at.date().isoformat()}:{content_hash[:12]}",
            quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
            content_hash=content_hash,
        )
