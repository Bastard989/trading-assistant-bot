from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


class EiaAdapter:
    source_code = "eia"

    def normalize_wti_90d_change(self, payload: bytes, *, fetched_at: datetime) -> list[Observation]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            document = json.loads(payload)
            if document.get("error"):
                raise SourcePayloadError("EIA returned an API error")
            rows = document["response"]["data"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourcePayloadError("invalid EIA response payload") from exc
        if not isinstance(rows, list):
            raise SourcePayloadError("EIA data must be a list")
        prices: dict[datetime, Decimal] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("series") != "RWTC":
                continue
            try:
                observed_date = datetime.strptime(row["period"], "%Y-%m-%d").date()
                value = Decimal(str(row["value"]))
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid EIA WTI row") from exc
            if not value.is_finite() or value <= 0:
                raise SourcePayloadError("EIA WTI price must be finite and positive")
            prices[datetime.combine(observed_date, time.min, tzinfo=timezone.utc)] = value
        dates = sorted(prices)
        content_hash = hashlib.sha256(payload).hexdigest()
        vintage = f"{fetched_at.date().isoformat()}:{content_hash[:12]}"
        observations = []
        for observed_at in dates:
            target = observed_at - timedelta(days=90)
            base_index = bisect_right(dates, target) - 1
            if base_index < 0:
                continue
            base_value = prices[dates[base_index]]
            change = ((prices[observed_at] / base_value) - 1) * 100
            observations.append(
                Observation(
                    indicator_code="wti_90d_change",
                    source_code=self.source_code,
                    value=change.quantize(Decimal("0.0001")),
                    unit="percent",
                    observed_at=observed_at,
                    released_at=observed_at,
                    fetched_at=fetched_at,
                    vintage=vintage,
                    quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
                    content_hash=content_hash,
                )
            )
        if not observations:
            raise SourcePayloadError("EIA response has insufficient history for a 90-day change")
        return observations
