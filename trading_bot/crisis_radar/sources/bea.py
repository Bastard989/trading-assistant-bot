from __future__ import annotations

import hashlib
import json
import re
from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")


class BeaAdapter:
    source_code = "bea"

    def normalize_real_gdp(self, payload: bytes, *, fetched_at: datetime) -> list[Observation]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            document = json.loads(payload)
            results = document["BEAAPI"]["Results"]
            if results.get("Error"):
                raise SourcePayloadError("BEA returned an API error")
            rows = results["Data"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourcePayloadError("invalid BEA response payload") from exc
        if not isinstance(rows, list):
            raise SourcePayloadError("BEA data must be a list")
        content_hash = hashlib.sha256(payload).hexdigest()
        vintage = f"{fetched_at.date().isoformat()}:{content_hash[:12]}"
        observations = []
        for row in rows:
            if not isinstance(row, dict) or row.get("TableName") != "T10101" or row.get("LineNumber") != "1":
                continue
            match = _QUARTER.match(str(row.get("TimePeriod", "")))
            if not match:
                raise SourcePayloadError("invalid BEA quarter")
            year, quarter = int(match.group(1)), int(match.group(2))
            month = quarter * 3
            try:
                value = Decimal(str(row["DataValue"]).replace(",", ""))
            except (InvalidOperation, KeyError) as exc:
                raise SourcePayloadError("invalid BEA GDP value") from exc
            observed_at = datetime(year, month, monthrange(year, month)[1], tzinfo=timezone.utc)
            if observed_at > fetched_at:
                continue
            observations.append(
                Observation(
                    indicator_code="us_real_gdp_qoq",
                    source_code=self.source_code,
                    value=value,
                    unit="percent_annualized",
                    observed_at=observed_at,
                    released_at=observed_at,
                    fetched_at=fetched_at,
                    vintage=vintage,
                    quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
                    content_hash=content_hash,
                )
            )
        if not observations:
            raise SourcePayloadError("BEA response contains no real GDP observations")
        return observations
