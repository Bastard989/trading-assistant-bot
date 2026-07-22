from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from calendar import monthrange
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation

from trading_bot.crisis_radar.domain import Observation, QualityFlag
from trading_bot.crisis_radar.sources.base import SourcePayloadError


_WORLD_BANK_SERIES = "NY.GDP.MKTP.KD.ZG"
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")
_BIS_CSV = "WS_CREDIT_GAP_csv_flat.csv"
_BIS_MAX_UNCOMPRESSED_BYTES = 20_000_000
_OECD_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class WorldBankAdapter:
    source_code = "world_bank"

    def normalize_gdp_growth(
        self,
        payload: bytes,
        *,
        country: str,
        fetched_at: datetime,
    ) -> list[Observation]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        country_code = country.strip().upper()
        indicator_code = {
            "CHN": "china_real_gdp_yoy",
            "WLD": "world_real_gdp_yoy",
        }.get(country_code)
        if indicator_code is None:
            raise ValueError("unsupported World Bank country code")
        try:
            document = json.loads(payload.decode("utf-8-sig"))
            metadata, rows = document
            last_updated = datetime.strptime(metadata["lastupdated"], "%Y-%m-%d").date()
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SourcePayloadError("invalid World Bank GDP payload") from exc
        if not isinstance(rows, list):
            raise SourcePayloadError("invalid World Bank GDP observations")
        released_at = datetime.combine(last_updated, time.min, tzinfo=timezone.utc)
        if released_at > fetched_at:
            released_at = fetched_at
        content_hash = hashlib.sha256(payload).hexdigest()
        vintage = f"{last_updated.isoformat()}:{content_hash[:12]}"
        observations = []
        for row in rows:
            try:
                if (
                    row["indicator"]["id"] != _WORLD_BANK_SERIES
                    or row["countryiso3code"] != country_code
                    or row["value"] is None
                ):
                    continue
                year = int(row["date"])
                value = Decimal(str(row["value"]))
            except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise SourcePayloadError("invalid World Bank GDP observation") from exc
            observed_at = datetime(year, 12, 31, tzinfo=timezone.utc)
            if observed_at > fetched_at:
                continue
            observations.append(
                Observation(
                    indicator_code=indicator_code,
                    source_code=self.source_code,
                    value=value,
                    unit="percent",
                    observed_at=observed_at,
                    released_at=released_at,
                    fetched_at=fetched_at,
                    vintage=vintage,
                    quality_flags=frozenset(),
                    content_hash=content_hash,
                )
            )
        if not observations:
            raise SourcePayloadError("World Bank response contains no GDP observations")
        return sorted(observations, key=lambda item: item.observed_at)


class BisAdapter:
    source_code = "bis"

    def normalize_credit_gaps(
        self, payload: bytes, *, fetched_at: datetime
    ) -> list[Observation]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries = archive.infolist()
                if len(entries) != 1 or entries[0].filename != _BIS_CSV:
                    raise SourcePayloadError("unexpected BIS archive contents")
                entry = entries[0]
                if entry.file_size > _BIS_MAX_UNCOMPRESSED_BYTES:
                    raise SourcePayloadError("BIS archive exceeds uncompressed size limit")
                csv_payload = archive.read(entry)
            text = csv_payload.decode("utf-8-sig")
            rows = csv.DictReader(io.StringIO(text))
            observations = self._normalize_rows(rows, payload=payload, fetched_at=fetched_at)
        except (zipfile.BadZipFile, UnicodeDecodeError, csv.Error) as exc:
            raise SourcePayloadError("invalid BIS credit-gap archive") from exc
        if not observations:
            raise SourcePayloadError("BIS response contains no selected credit-gap observations")
        return sorted(observations, key=lambda item: (item.indicator_code, item.observed_at))

    def _normalize_rows(
        self,
        rows: csv.DictReader,
        *,
        payload: bytes,
        fetched_at: datetime,
    ) -> list[Observation]:
        required = {
            "FREQ:Frequency",
            "BORROWERS_CTY:Borrowers' country",
            "TC_BORROWERS:Borrowing sector",
            "TC_LENDERS:Lending sector",
            "CG_DTYPE:Credit gap data type",
            "TIME_PERIOD:Time period or range",
            "OBS_VALUE:Observation Value",
        }
        if rows.fieldnames is None or not required.issubset(rows.fieldnames):
            raise SourcePayloadError("BIS credit-gap CSV is missing required columns")
        content_hash = hashlib.sha256(payload).hexdigest()
        vintage = f"{fetched_at.date().isoformat()}:{content_hash[:12]}"
        country_mapping = {
            "US: United States": "us_credit_to_gdp_gap",
            "CN: China": "china_credit_to_gdp_gap",
        }
        result = []
        for row in rows:
            indicator_code = country_mapping.get(row["BORROWERS_CTY:Borrowers' country"])
            if (
                indicator_code is None
                or row["FREQ:Frequency"] != "Q: Quarterly"
                or row["TC_BORROWERS:Borrowing sector"]
                != "P: Private non-financial sector"
                or row["TC_LENDERS:Lending sector"] != "A: All sectors"
                or row["CG_DTYPE:Credit gap data type"]
                != "C: Credit-to-GDP gaps (actual-trend)"
                or not row["OBS_VALUE:Observation Value"]
            ):
                continue
            match = _QUARTER.match(row["TIME_PERIOD:Time period or range"])
            if not match:
                raise SourcePayloadError("invalid BIS credit-gap quarter")
            year, quarter = int(match.group(1)), int(match.group(2))
            month = quarter * 3
            try:
                value = Decimal(row["OBS_VALUE:Observation Value"])
            except InvalidOperation as exc:
                raise SourcePayloadError("invalid BIS credit-gap value") from exc
            observed_at = datetime(
                year,
                month,
                monthrange(year, month)[1],
                tzinfo=timezone.utc,
            )
            if observed_at > fetched_at:
                continue
            result.append(
                Observation(
                    indicator_code=indicator_code,
                    source_code=self.source_code,
                    value=value,
                    unit="percentage_points",
                    observed_at=observed_at,
                    released_at=fetched_at,
                    fetched_at=fetched_at,
                    vintage=vintage,
                    quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
                    content_hash=content_hash,
                )
            )
        return result


class OecdAdapter:
    source_code = "oecd"

    def normalize_cli_momentum(
        self, payload: bytes, *, fetched_at: datetime
    ) -> list[Observation]:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        try:
            rows = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
            observations = self._normalize_cli_rows(rows, payload=payload, fetched_at=fetched_at)
        except (UnicodeDecodeError, csv.Error) as exc:
            raise SourcePayloadError("invalid OECD CLI CSV") from exc
        if not observations:
            raise SourcePayloadError("OECD response contains no usable CLI momentum observations")
        return sorted(observations, key=lambda item: (item.indicator_code, item.observed_at))

    def _normalize_cli_rows(
        self,
        rows: csv.DictReader,
        *,
        payload: bytes,
        fetched_at: datetime,
    ) -> list[Observation]:
        required = {
            "DATAFLOW",
            "REF_AREA",
            "FREQ",
            "MEASURE",
            "UNIT_MEASURE",
            "ADJUSTMENT",
            "TRANSFORMATION",
            "METHODOLOGY",
            "TIME_PERIOD",
            "OBS_VALUE",
        }
        if rows.fieldnames is None or not required.issubset(rows.fieldnames):
            raise SourcePayloadError("OECD CLI CSV is missing required columns")
        values: dict[str, dict[tuple[int, int], Decimal]] = {"G20": {}, "CHN": {}}
        for row in rows:
            area = row.get("REF_AREA", "")
            if area not in values:
                continue
            if (
                not row.get("DATAFLOW", "").startswith("OECD.SDD.STES:DSD_STES@DF_CLI")
                or row.get("FREQ") != "M"
                or row.get("MEASURE") != "LI"
                or row.get("UNIT_MEASURE") != "IX"
                or row.get("ADJUSTMENT") != "AA"
                or row.get("TRANSFORMATION") != "IX"
                or row.get("METHODOLOGY") != "H"
            ):
                raise SourcePayloadError("unexpected OECD CLI dimensions")
            match = _OECD_MONTH.match(row.get("TIME_PERIOD", ""))
            if not match:
                raise SourcePayloadError("invalid OECD CLI month")
            key = (int(match.group(1)), int(match.group(2)))
            if key in values[area]:
                raise SourcePayloadError("duplicate OECD CLI observation")
            try:
                values[area][key] = Decimal(row["OBS_VALUE"])
            except (InvalidOperation, KeyError) as exc:
                raise SourcePayloadError("invalid OECD CLI value") from exc

        content_hash = hashlib.sha256(payload).hexdigest()
        vintage = f"{fetched_at.date().isoformat()}:{content_hash[:12]}"
        indicator_codes = {
            "G20": "g20_cli_6m_change",
            "CHN": "china_cli_6m_change",
        }
        result = []
        for area, series in values.items():
            for (year, month), current in sorted(series.items()):
                previous_month = month - 6
                previous_year = year
                if previous_month <= 0:
                    previous_month += 12
                    previous_year -= 1
                previous = series.get((previous_year, previous_month))
                if previous is None:
                    continue
                observed_at = datetime(
                    year,
                    month,
                    monthrange(year, month)[1],
                    tzinfo=timezone.utc,
                )
                if observed_at > fetched_at:
                    continue
                result.append(
                    Observation(
                        indicator_code=indicator_codes[area],
                        source_code=self.source_code,
                        value=(current - previous).quantize(Decimal("0.0001")),
                        unit="index_points_6m",
                        observed_at=observed_at,
                        released_at=fetched_at,
                        fetched_at=fetched_at,
                        vintage=vintage,
                        quality_flags=frozenset({QualityFlag.RELEASE_TIME_ESTIMATED}),
                        content_hash=content_hash,
                    )
                )
        return result
