from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


ECB_CISS_KEY = "CISS.D.U2.Z0Z.4F.EC.SS_CIN.IDX"


class EcbAdapter:
    source_code = "ecb"

    def normalize_ciss(self, payload: bytes, *, fetched_at: datetime) -> list[Observation]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            text = payload.decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise SourcePayloadError("invalid ECB CISS CSV payload") from exc
        required = {"KEY", "TIME_PERIOD", "OBS_VALUE"}
        if not rows or not required.issubset(rows[0]):
            raise SourcePayloadError("ECB CISS CSV is missing required columns")
        content_hash = hashlib.sha256(payload).hexdigest()
        vintage = f"{fetched_at.date().isoformat()}:{content_hash[:12]}"
        observations = []
        for row in rows:
            if row.get("KEY") != ECB_CISS_KEY or not row.get("OBS_VALUE"):
                continue
            try:
                observed_date = datetime.strptime(row["TIME_PERIOD"], "%Y-%m-%d").date()
                value = Decimal(row["OBS_VALUE"])
            except (InvalidOperation, ValueError) as exc:
                raise SourcePayloadError("invalid ECB CISS observation") from exc
            observed_at = datetime.combine(observed_date, time.min, tzinfo=timezone.utc)
            if observed_at > fetched_at:
                continue
            observations.append(
                Observation(
                    indicator_code="euro_ciss",
                    source_code=self.source_code,
                    value=value,
                    unit="index",
                    observed_at=observed_at,
                    released_at=observed_at,
                    fetched_at=fetched_at,
                    vintage=vintage,
                    quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
                    content_hash=content_hash,
                )
            )
        if not observations:
            raise SourcePayloadError("ECB response contains no CISS observations")
        return observations
