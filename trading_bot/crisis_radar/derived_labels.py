from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .event_catalog import HistoricalEventLabel


UTC = timezone.utc
MINIMUM_HISTORY_DAYS = 365
MERGE_WINDOW_DAYS = 7
BTC_RETURN_PERCENTILE = Decimal("0.05")
OI_CHANGE_PERCENTILE = Decimal("0.05")
PRIOR_OI_PERCENTILE = Decimal("0.80")
DEFAULT_SOURCE_URL = "https://bybit-exchange.github.io/docs/v5/market/open-interest"


def _require_decimal(value: Decimal | None, field: str) -> None:
    if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
        raise ValueError(f"{field} must be a finite Decimal or None")


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(frozen=True)
class CryptoDailyRecord:
    """One completed UTC day of inputs used by the derived-label rule.

    Seven-day returns and changes are inputs rather than recomputed here so a
    caller can preserve the exact, versioned market-data transformation used.
    """

    observed_at: datetime
    btc_price: Decimal | None
    btc_return_7d: Decimal | None
    oi_level: Decimal | None
    oi_change_7d: Decimal | None
    funding: Decimal | None
    eth_breadth: Decimal | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("observed_at must be UTC-aware")
        utc_value = self.observed_at.astimezone(UTC)
        if utc_value.time() != datetime.min.time():
            raise ValueError("observed_at must be a UTC day boundary")
        for field in (
            "btc_price",
            "btc_return_7d",
            "oi_level",
            "oi_change_7d",
            "funding",
            "eth_breadth",
        ):
            _require_decimal(getattr(self, field), field)
        if self.btc_price is not None and self.btc_price <= 0:
            raise ValueError("btc_price must be positive")
        if self.oi_level is not None and self.oi_level < 0:
            raise ValueError("oi_level must not be negative")

    def canonical_payload(self) -> dict[str, str | None]:
        return {
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "btc_price": _decimal_text(self.btc_price),
            "btc_return_7d": _decimal_text(self.btc_return_7d),
            "oi_level": _decimal_text(self.oi_level),
            "oi_change_7d": _decimal_text(self.oi_change_7d),
            "funding": _decimal_text(self.funding),
            "eth_breadth": _decimal_text(self.eth_breadth),
        }


@dataclass(frozen=True)
class DerivedLabelEvaluation:
    observed_at: datetime
    sufficient: bool
    candidate: bool | None
    reasons: tuple[str, ...]
    btc_return_threshold: Decimal | None = None
    oi_change_threshold: Decimal | None = None
    prior_oi_threshold: Decimal | None = None
    prior_oi_level: Decimal | None = None
    prior_funding_sum: Decimal | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "sufficient": self.sufficient,
            "candidate": self.candidate,
            "reasons": list(self.reasons),
            "btc_return_threshold": _decimal_text(self.btc_return_threshold),
            "oi_change_threshold": _decimal_text(self.oi_change_threshold),
            "prior_oi_threshold": _decimal_text(self.prior_oi_threshold),
            "prior_oi_level": _decimal_text(self.prior_oi_level),
            "prior_funding_sum": _decimal_text(self.prior_funding_sum),
        }


@dataclass(frozen=True)
class DerivedLabelResult:
    definition: dict[str, Any]
    input_checksum: str
    evaluations: tuple[DerivedLabelEvaluation, ...]
    labels: tuple[HistoricalEventLabel, ...]

    @property
    def status(self) -> str:
        return "derived"

    @property
    def official(self) -> bool:
        return False

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "definition": self.definition,
            "input_checksum": self.input_checksum,
            "status": self.status,
            "official": self.official,
            "evaluations": [item.canonical_payload() for item in self.evaluations],
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


def crypto_leverage_unwind_definition() -> dict[str, Any]:
    return {
        "code": "crypto-leverage-unwind-derived-v1",
        "scenario_code": "crypto_leverage_unwind",
        "status": "derived",
        "official": False,
        "source_market": "Bybit",
        "minimum_prior_observations": MINIMUM_HISTORY_DAYS,
        "percentile_method": "linear_interpolation_sorted_sample",
        "timing": {
            "percentile_samples": "strictly_before_candidate_day_t",
            "prior_oi_reference": "t_minus_7_completed_utc_days",
            "prior_funding_window": "t_minus_7_through_t_minus_1_completed_utc_days",
        },
        "candidate_rule": {
            "btc_return_7d": "less_than_or_equal_to_expanding_prior_5th_percentile",
            "oi_change_7d": "less_than_or_equal_to_expanding_prior_5th_percentile",
            "prior_leverage_build_up": {
                "operator": "or",
                "oi_level": "t_minus_7_greater_than_or_equal_to_expanding_prior_80th_percentile",
                "funding": "previous_7d_sum_greater_than_zero",
            },
        },
        "missing_data_policy": "insufficient_not_negative",
        "merge_trigger_distance_days": MERGE_WINDOW_DAYS,
        "eth_breadth": "optional_context_only_not_part_of_v1_trigger",
        "limitation": (
            "This is a reproducible research label derived from market data, "
            "not an official crisis declaration or a trading recommendation."
        ),
    }


def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = Decimal(len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def _checksum_payload(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _insufficient(record: CryptoDailyRecord, *reasons: str) -> DerivedLabelEvaluation:
    return DerivedLabelEvaluation(
        observed_at=record.observed_at,
        sufficient=False,
        candidate=None,
        reasons=tuple(reasons),
    )


def _merge_labels(
    trigger_dates: list[datetime],
    *,
    source_url: str,
) -> tuple[HistoricalEventLabel, ...]:
    if not trigger_dates:
        return ()
    groups: list[list[datetime]] = [[trigger_dates[0]]]
    for trigger in trigger_dates[1:]:
        if trigger - groups[-1][-1] <= timedelta(days=MERGE_WINDOW_DAYS):
            groups[-1].append(trigger)
        else:
            groups.append([trigger])
    return tuple(
        HistoricalEventLabel(
            code=f"crypto-leverage-unwind-derived-{group[0]:%Y%m%d}",
            started_at=group[0],
            ended_at=group[-1],
            start_precision="day",
            end_precision="day",
            region_code="GLOBAL",
            source_url=source_url,
            source_note=(
                f"Derived Bybit-market rule v1; {len(group)} trigger day(s) merged "
                f"with a maximum adjacent distance of {MERGE_WINDOW_DAYS} days."
            ),
            status="derived",
        )
        for group in groups
    )


def generate_crypto_leverage_unwind_labels(
    records: tuple[CryptoDailyRecord, ...] | list[CryptoDailyRecord],
    *,
    source_url: str = DEFAULT_SOURCE_URL,
) -> DerivedLabelResult:
    """Apply the versioned rule without using any observation after day *t*."""

    if not source_url.startswith("https://") or len(source_url) > 1000:
        raise ValueError("source_url must be a bounded HTTPS URL")
    ordered = sorted(records, key=lambda item: item.observed_at)
    if len({item.observed_at for item in ordered}) != len(ordered):
        raise ValueError("records must contain unique UTC days")

    by_day = {item.observed_at: item for item in ordered}
    evaluations: list[DerivedLabelEvaluation] = []
    triggers: list[datetime] = []

    for index, record in enumerate(ordered):
        current_missing = tuple(
            field
            for field in ("btc_price", "btc_return_7d", "oi_level", "oi_change_7d", "funding")
            if getattr(record, field) is None
        )
        if current_missing:
            evaluations.append(
                _insufficient(record, *(f"missing_current_{field}" for field in current_missing))
            )
            continue

        previous = ordered[:index]
        historical_returns = [item.btc_return_7d for item in previous if item.btc_return_7d is not None]
        historical_oi_changes = [item.oi_change_7d for item in previous if item.oi_change_7d is not None]
        historical_oi = [item.oi_level for item in previous if item.oi_level is not None]
        missing_history = []
        if len(historical_returns) < MINIMUM_HISTORY_DAYS:
            missing_history.append("insufficient_btc_return_history")
        if len(historical_oi_changes) < MINIMUM_HISTORY_DAYS:
            missing_history.append("insufficient_oi_change_history")
        if len(historical_oi) < MINIMUM_HISTORY_DAYS:
            missing_history.append("insufficient_oi_level_history")
        if missing_history:
            evaluations.append(_insufficient(record, *missing_history))
            continue

        prior_days = [record.observed_at - timedelta(days=days) for days in range(7, 0, -1)]
        prior_records = [by_day.get(day) for day in prior_days]
        if any(item is None for item in prior_records):
            evaluations.append(_insufficient(record, "missing_previous_7d_record"))
            continue
        completed_prior = [item for item in prior_records if item is not None]
        if any(item.funding is None for item in completed_prior):
            evaluations.append(_insufficient(record, "missing_previous_7d_funding"))
            continue
        prior_oi = completed_prior[0].oi_level
        if prior_oi is None:
            evaluations.append(_insufficient(record, "missing_prior_oi_level"))
            continue

        btc_threshold = _percentile(historical_returns, BTC_RETURN_PERCENTILE)
        oi_change_threshold = _percentile(historical_oi_changes, OI_CHANGE_PERCENTILE)
        oi_level_threshold = _percentile(historical_oi, PRIOR_OI_PERCENTILE)
        funding_sum = sum((item.funding for item in completed_prior if item.funding is not None), Decimal(0))
        leverage_build_up = prior_oi >= oi_level_threshold or funding_sum > 0
        candidate = bool(
            record.btc_return_7d <= btc_threshold
            and record.oi_change_7d <= oi_change_threshold
            and leverage_build_up
        )
        reasons = (
            ("trigger_rule_satisfied",)
            if candidate
            else ("trigger_rule_not_satisfied",)
        )
        evaluations.append(
            DerivedLabelEvaluation(
                observed_at=record.observed_at,
                sufficient=True,
                candidate=candidate,
                reasons=reasons,
                btc_return_threshold=btc_threshold,
                oi_change_threshold=oi_change_threshold,
                prior_oi_threshold=oi_level_threshold,
                prior_oi_level=prior_oi,
                prior_funding_sum=funding_sum,
            )
        )
        if candidate:
            triggers.append(record.observed_at)

    input_payload = [item.canonical_payload() for item in ordered]
    return DerivedLabelResult(
        definition=crypto_leverage_unwind_definition(),
        input_checksum=_checksum_payload(input_payload),
        evaluations=tuple(evaluations),
        labels=_merge_labels(triggers, source_url=source_url),
    )
