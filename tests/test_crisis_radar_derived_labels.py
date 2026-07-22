from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_bot.crisis_radar.derived_labels import (
    CryptoDailyRecord,
    generate_crypto_leverage_unwind_labels,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


UTC = timezone.utc
START = datetime(2020, 1, 1, tzinfo=UTC)


def _records(
    *,
    count: int = 390,
    triggers: tuple[int, ...] = (),
    missing: dict[tuple[int, str], None] | None = None,
) -> list[CryptoDailyRecord]:
    missing = missing or {}
    rows = []
    for index in range(count):
        historical_value = Decimal(index) if index < 365 else Decimal("1000")
        values = {
            "btc_price": Decimal("50000") + Decimal(index),
            "btc_return_7d": historical_value,
            "oi_level": Decimal("10"),
            "oi_change_7d": historical_value,
            "funding": Decimal("0"),
            "eth_breadth": Decimal("0.5"),
        }
        if index in triggers:
            values["btc_return_7d"] = Decimal("-100")
            values["oi_change_7d"] = Decimal("-100")
        for field in tuple(values):
            if (index, field) in missing:
                values[field] = None
        rows.append(
            CryptoDailyRecord(
                observed_at=START + timedelta(days=index),
                **values,
            )
        )
    return rows


def _evaluation(result, index: int):
    observed_at = START + timedelta(days=index)
    return next(item for item in result.evaluations if item.observed_at == observed_at)


def test_expanding_thresholds_never_look_ahead() -> None:
    records = _records(count=380, triggers=(370,))
    before = generate_crypto_leverage_unwind_labels(records)

    # Extreme future values must not affect the threshold or decision at t=370.
    changed_future = list(records)
    for index in range(371, len(changed_future)):
        row = changed_future[index]
        changed_future[index] = CryptoDailyRecord(
            observed_at=row.observed_at,
            btc_price=row.btc_price,
            btc_return_7d=Decimal("-999999"),
            oi_level=Decimal("999999"),
            oi_change_7d=Decimal("-999999"),
            funding=Decimal("1"),
            eth_breadth=row.eth_breadth,
        )
    after = generate_crypto_leverage_unwind_labels(changed_future)

    before_at_t = _evaluation(before, 370)
    after_at_t = _evaluation(after, 370)
    assert before_at_t == after_at_t
    assert before_at_t.candidate is True
    assert before_at_t.btc_return_threshold == Decimal("18.45")
    assert before_at_t.oi_change_threshold == Decimal("18.45")


def test_missing_inputs_are_insufficient_instead_of_negative() -> None:
    current_missing = generate_crypto_leverage_unwind_labels(
        _records(triggers=(370,), missing={(370, "funding"): None})
    )
    evaluation = _evaluation(current_missing, 370)
    assert evaluation.sufficient is False
    assert evaluation.candidate is None
    assert "missing_current_funding" in evaluation.reasons

    prior_missing = generate_crypto_leverage_unwind_labels(
        _records(triggers=(370,), missing={(367, "funding"): None})
    )
    prior_evaluation = _evaluation(prior_missing, 370)
    assert prior_evaluation.sufficient is False
    assert prior_evaluation.candidate is None
    assert prior_evaluation.reasons == ("missing_previous_7d_funding",)


def test_trigger_days_within_seven_days_are_merged() -> None:
    result = generate_crypto_leverage_unwind_labels(_records(triggers=(370, 377, 385)))

    assert len(result.labels) == 2
    assert result.labels[0].started_at == START + timedelta(days=370)
    assert result.labels[0].ended_at == START + timedelta(days=377)
    assert result.labels[1].started_at == START + timedelta(days=385)
    assert result.labels[1].ended_at == START + timedelta(days=385)
    assert all(item.status == "derived" for item in result.labels)
    assert result.status == "derived"
    assert result.official is False
    assert result.definition["official"] is False


def test_input_order_does_not_change_labels_or_checksums() -> None:
    records = _records(triggers=(370, 377))

    chronological = generate_crypto_leverage_unwind_labels(records)
    reversed_input = generate_crypto_leverage_unwind_labels(list(reversed(records)))

    assert chronological.input_checksum == reversed_input.input_checksum
    assert chronological.checksum == reversed_input.checksum
    assert chronological.labels == reversed_input.labels
    assert chronological.evaluations == reversed_input.evaluations


def test_records_require_utc_day_boundaries_and_decimal_values() -> None:
    with pytest.raises(ValueError, match="finite Decimal"):
        CryptoDailyRecord(
            observed_at=START,
            btc_price=50000.0,  # type: ignore[arg-type]
            btc_return_7d=Decimal("1"),
            oi_level=Decimal("1"),
            oi_change_7d=Decimal("1"),
            funding=Decimal("0"),
        )
    with pytest.raises(ValueError, match="UTC day boundary"):
        CryptoDailyRecord(
            observed_at=START + timedelta(hours=1),
            btc_price=Decimal("50000"),
            btc_return_7d=Decimal("1"),
            oi_level=Decimal("1"),
            oi_change_7d=Decimal("1"),
            funding=Decimal("0"),
        )
    with pytest.raises(ValueError, match="UTC-aware"):
        CryptoDailyRecord(
            observed_at=datetime(2020, 1, 1, tzinfo=timezone(timedelta(hours=3))),
            btc_price=Decimal("50000"),
            btc_return_7d=Decimal("1"),
            oi_level=Decimal("1"),
            oi_change_7d=Decimal("1"),
            funding=Decimal("0"),
        )

    exact = generate_crypto_leverage_unwind_labels(_records(count=371, triggers=(370,)))
    assert _evaluation(exact, 370).btc_return_threshold == Decimal("18.45")


def test_duplicate_utc_days_are_rejected() -> None:
    records = _records(count=2)
    with pytest.raises(ValueError, match="unique UTC days"):
        generate_crypto_leverage_unwind_labels([records[0], records[0]])


def test_derived_catalog_is_persisted_separately_from_official_gap(tmp_path) -> None:
    database = Database(tmp_path / "derived-catalog.sqlite3")
    repository = CrisisRadarRepository(database)
    service = CrisisRadarService(repository)
    service.bootstrap()
    codes = {
        "btc_close_price": ("USDT", lambda index: Decimal("50000") + index),
        "btc_return_7d": (
            "percent",
            lambda index: Decimal("-100") if index == 370 else Decimal(index),
        ),
        "btc_open_interest": ("coin", lambda index: Decimal("10")),
        "btc_oi_7d_change": (
            "percent",
            lambda index: Decimal("-100") if index == 370 else Decimal(index),
        ),
        "btc_funding_rate": ("percent", lambda _index: Decimal("0")),
        "eth_return_7d": ("percent", lambda _index: Decimal("0")),
    }
    fetched_at = START + timedelta(days=500)
    with database.connect() as connection:
        source_id = connection.execute(
            "SELECT id FROM cr_sources WHERE code = 'bybit'"
        ).fetchone()[0]
        indicator_ids = {
            row["code"]: row["id"]
            for row in connection.execute(
                "SELECT id, code FROM cr_indicator_definitions"
            ).fetchall()
        }
        rows = []
        for index in range(371):
            observed_at = START + timedelta(days=index)
            for code, (unit, value) in codes.items():
                rows.append(
                    (
                        indicator_ids[code],
                        source_id,
                        observed_at.isoformat(),
                        observed_at.isoformat(),
                        fetched_at.isoformat(),
                        format(value(index), "f"),
                        unit,
                        "fixture-v1",
                    )
                )
        connection.executemany(
            """
            INSERT INTO cr_observations(
                indicator_id, source_id, observed_at, released_at, fetched_at,
                value_text, unit, vintage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    summary = service.derive_crypto_event_catalog(
        effective_at=datetime(2026, 7, 21, tzinfo=UTC)
    )

    assert summary["record_count"] == 371
    assert summary["sufficient_count"] == 6
    assert summary["label_count"] == 1
    derived = service.event_catalog(
        "crypto_leverage_unwind", version=str(summary["version"])
    )
    assert derived is not None
    assert derived["version"].startswith("bybit-derived-v1-20260721-")
    assert derived["definition"]["official"] is False
    assert derived["labels"][0]["label_status"] == "derived"
    official_gap = service.event_catalog(
        "crypto_leverage_unwind", version="official-source-gap-2026-v1"
    )
    assert official_gap is not None
    assert official_gap["labels"] == []
