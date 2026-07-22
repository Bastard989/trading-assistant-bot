from __future__ import annotations

import hashlib
import json
import re
from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")


class EurostatAdapter:
    source_code = "eurostat"

    def normalize_real_gdp(self, payload: bytes, *, fetched_at: datetime) -> list[Observation]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            document = json.loads(payload)
            dimensions = document["dimension"]
            time_index = dimensions["time"]["category"]["index"]
            values = document["value"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourcePayloadError("invalid Eurostat GDP payload") from exc
        expected = ["freq", "unit", "s_adj", "na_item", "geo", "time"]
        if document.get("id") != expected or not isinstance(time_index, dict) or not isinstance(values, dict):
            raise SourcePayloadError("unexpected Eurostat GDP dimensions")
        content_hash = hashlib.sha256(payload).hexdigest()
        vintage = f"{fetched_at.date().isoformat()}:{content_hash[:12]}"
        observations = []
        for period, position in sorted(time_index.items(), key=lambda item: item[1]):
            raw_value = values.get(str(position))
            if raw_value is None:
                continue
            match = _QUARTER.match(period)
            if not match:
                raise SourcePayloadError("invalid Eurostat quarter")
            year, quarter = int(match.group(1)), int(match.group(2))
            month = quarter * 3
            try:
                value = Decimal(str(raw_value))
            except InvalidOperation as exc:
                raise SourcePayloadError("invalid Eurostat GDP value") from exc
            observed_at = datetime(year, month, monthrange(year, month)[1], tzinfo=timezone.utc)
            if observed_at > fetched_at:
                continue
            observations.append(
                Observation(
                    indicator_code="euro_real_gdp_qoq",
                    source_code=self.source_code,
                    value=value,
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
            raise SourcePayloadError("Eurostat response contains no GDP observations")
        return observations
