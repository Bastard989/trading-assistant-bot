from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime


SUPPORTED_SCENARIOS = frozenset(
    {
        "global_recession",
        "financial_stress",
        "oil_stagflation",
        "crypto_leverage_unwind",
        "china_hard_landing",
    }
)
TIME_PRECISIONS = frozenset({"instant", "day", "month", "year"})
LABEL_STATUSES = frozenset({"confirmed", "derived"})


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _https(value: str, field: str) -> None:
    if not value.startswith("https://") or len(value) > 1000:
        raise ValueError(f"{field} must be a bounded HTTPS URL")


@dataclass(frozen=True)
class HistoricalEventLabel:
    code: str
    started_at: datetime
    ended_at: datetime | None
    start_precision: str
    end_precision: str | None
    region_code: str
    source_url: str
    source_note: str = ""
    status: str = "confirmed"

    def __post_init__(self) -> None:
        if not self.code.strip() or len(self.code) > 120:
            raise ValueError("event label code must contain 1-120 characters")
        _aware(self.started_at, "started_at")
        if self.ended_at is not None:
            _aware(self.ended_at, "ended_at")
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must not precede started_at")
        if self.start_precision not in TIME_PRECISIONS:
            raise ValueError("unsupported start precision")
        if self.end_precision is not None and self.end_precision not in TIME_PRECISIONS:
            raise ValueError("unsupported end precision")
        if self.ended_at is None and self.end_precision is not None:
            raise ValueError("end_precision requires ended_at")
        if not self.region_code.strip() or len(self.region_code) > 32:
            raise ValueError("region_code must contain 1-32 characters")
        _https(self.source_url, "source_url")
        if len(self.source_note) > 2000:
            raise ValueError("source_note is too long")
        if self.status not in LABEL_STATUSES:
            raise ValueError("unsupported event label status")

    def canonical_payload(self) -> dict:
        return {
            "code": self.code,
            "started_at": self.started_at.isoformat(),
            "ended_at": None if self.ended_at is None else self.ended_at.isoformat(),
            "start_precision": self.start_precision,
            "end_precision": self.end_precision,
            "region_code": self.region_code,
            "source_url": self.source_url,
            "source_note": self.source_note,
            "status": self.status,
        }


@dataclass(frozen=True)
class EventCatalogVersion:
    scenario_code: str
    version: str
    source_name: str
    source_url: str
    definition: dict
    limitations: tuple[str, ...]
    effective_from: datetime
    labels: tuple[HistoricalEventLabel, ...]

    def __post_init__(self) -> None:
        if self.scenario_code not in SUPPORTED_SCENARIOS:
            raise ValueError("unsupported event catalog scenario")
        if not self.version.strip() or len(self.version) > 80:
            raise ValueError("catalog version must contain 1-80 characters")
        if not self.source_name.strip() or len(self.source_name) > 240:
            raise ValueError("source_name must contain 1-240 characters")
        _https(self.source_url, "source_url")
        _aware(self.effective_from, "effective_from")
        if not isinstance(self.definition, dict):
            raise ValueError("definition must be an object")
        if len(json.dumps(self.definition, ensure_ascii=False)) > 30000:
            raise ValueError("catalog definition is too large")
        if any(not item.strip() or len(item) > 2000 for item in self.limitations):
            raise ValueError("catalog limitations must be non-empty bounded strings")
        codes = [item.code for item in self.labels]
        if len(codes) != len(set(codes)):
            raise ValueError("event label codes must be unique")
        ordered = sorted(self.labels, key=lambda item: (item.started_at, item.code))
        if list(self.labels) != ordered:
            raise ValueError("event labels must be ordered by start time and code")
        for previous, current in zip(self.labels, self.labels[1:]):
            if previous.ended_at is None or current.started_at <= previous.ended_at:
                raise ValueError("event labels must not overlap")

    def canonical_payload(self) -> dict:
        return {
            "scenario_code": self.scenario_code,
            "version": self.version,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "definition": self.definition,
            "limitations": list(self.limitations),
            "effective_from": self.effective_from.isoformat(),
            "labels": [item.canonical_payload() for item in self.labels],
        }

    @property
    def checksum(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()
