from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SeriesRequest, SourcePayloadError


class FredAdapter:
    source_code = "fred"

    def normalize(
        self,
        payload: bytes,
        request: SeriesRequest,
        *,
        fetched_at: datetime,
        release_from_vintage: bool = False,
    ) -> list[Observation]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            document = json.loads(payload)
            rows = document["observations"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourcePayloadError("invalid FRED observations payload") from exc
        if not isinstance(rows, list):
            raise SourcePayloadError("FRED observations must be a list")

        content_hash = hashlib.sha256(payload).hexdigest()
        result: list[Observation] = []
        for row in rows:
            if not isinstance(row, dict):
                raise SourcePayloadError("invalid FRED observation row")
            raw_value = row.get("value")
            if raw_value in {None, "."}:
                continue
            try:
                value = Decimal(str(raw_value))
                observed_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
                vintage_date = datetime.strptime(row["realtime_start"], "%Y-%m-%d").date()
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid FRED observation value or date") from exc
            if not value.is_finite():
                raise SourcePayloadError("FRED observation value must be finite")
            observed_at = datetime.combine(observed_date, time.min, tzinfo=timezone.utc)
            # FRED's realtime_start identifies the data vintage, not the original
            # publication timestamp. Until a release-calendar adapter is available,
            # observed_at is the conservative freshness anchor and is explicitly
            # marked as estimated below.
            released_at = (
                datetime.combine(vintage_date, time.min, tzinfo=timezone.utc)
                if release_from_vintage
                else observed_at
            )
            if released_at < observed_at:
                raise SourcePayloadError("FRED initial release cannot precede observation date")
            if released_at > fetched_at:
                raise SourcePayloadError("FRED observation cannot be later than fetch time")
            result.append(
                Observation(
                    indicator_code=request.indicator_code,
                    source_code=self.source_code,
                    value=value,
                    unit=request.unit,
                    observed_at=observed_at,
                    released_at=released_at,
                    fetched_at=fetched_at,
                    vintage=vintage_date.isoformat(),
                    quality_flags=(
                        frozenset()
                        if release_from_vintage
                        else frozenset({QualityFlag.RELEASE_TIME_ESTIMATED})
                    ),
                    content_hash=content_hash,
                )
            )
        return result


class FredTransformAdapter:
    source_code = "fred"

    def normalize(
        self,
        payload: bytes,
        request: SeriesRequest,
        *,
        transform: str,
        fetched_at: datetime,
        release_from_vintage: bool = False,
    ) -> list[Observation]:
        raw = FredAdapter().normalize(
            payload,
            SeriesRequest("_raw", request.provider_series_id, "raw"),
            fetched_at=fetched_at,
            release_from_vintage=release_from_vintage,
        )
        raw = sorted(raw, key=lambda item: item.observed_at)
        if transform == "drawdown_30d":
            return self._drawdown(raw, request, window=timedelta(days=30))
        if transform == "change_90d":
            return self._change(raw, request, window=timedelta(days=90))
        if transform == "change_30d":
            return self._change(raw, request, window=timedelta(days=30))
        if transform == "change_180d":
            return self._change(raw, request, window=timedelta(days=180))
        if transform == "difference_1_period":
            return self._difference_one_period(raw, request)
        raise SourcePayloadError(f"unsupported FRED transform: {transform}")

    @staticmethod
    def _derived(
        source: Observation,
        request: SeriesRequest,
        value: Decimal,
    ) -> Observation:
        return Observation(
            indicator_code=request.indicator_code,
            source_code="fred",
            value=value.quantize(Decimal("0.0001")),
            unit=request.unit,
            observed_at=source.observed_at,
            released_at=source.released_at,
            fetched_at=source.fetched_at,
            vintage=source.vintage,
            quality_flags=source.quality_flags,
            content_hash=source.content_hash,
        )

    def _drawdown(
        self,
        raw: list[Observation],
        request: SeriesRequest,
        *,
        window: timedelta,
    ) -> list[Observation]:
        dates = [item.observed_at for item in raw]
        result = []
        for index, item in enumerate(raw):
            start = bisect_right(dates, item.observed_at - window)
            window_items = raw[max(0, start - 1) : index + 1]
            if len(window_items) < 2:
                continue
            peak = max(candidate.value for candidate in window_items)
            value = (item.value / peak - 1) * 100
            result.append(self._derived(item, request, value))
        if not result:
            raise SourcePayloadError("FRED response has insufficient history for drawdown")
        return result

    def _change(
        self,
        raw: list[Observation],
        request: SeriesRequest,
        *,
        window: timedelta,
    ) -> list[Observation]:
        dates = [item.observed_at for item in raw]
        result = []
        for item in raw:
            base_index = bisect_right(dates, item.observed_at - window) - 1
            if base_index < 0:
                continue
            base = raw[base_index].value
            if base == 0:
                raise SourcePayloadError("FRED transform base value cannot be zero")
            value = (item.value / base - 1) * 100
            result.append(self._derived(item, request, value))
        if not result:
            raise SourcePayloadError("FRED response has insufficient history for change")
        return result

    def _difference_one_period(
        self,
        raw: list[Observation],
        request: SeriesRequest,
    ) -> list[Observation]:
        if len(raw) < 2:
            raise SourcePayloadError("FRED response has insufficient history for difference")
        return [
            self._derived(item, request, item.value - raw[index - 1].value)
            for index, item in enumerate(raw)
            if index > 0
        ]
