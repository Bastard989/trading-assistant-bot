import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading_bot.crisis_radar.sources.base import SeriesRequest, SourcePayloadError
from trading_bot.crisis_radar.sources.fred import FredTransformAdapter


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def test_sp500_transform_calculates_drawdown_without_future_data() -> None:
    observations = FredTransformAdapter().normalize(
        (FIXTURES / "fred_sp500_history.json").read_bytes(),
        SeriesRequest("sp500_30d_drawdown", "SP500", "percent"),
        transform="drawdown_30d",
        fetched_at=NOW,
    )

    assert observations[-1].observed_at == datetime(2026, 7, 18, tzinfo=timezone.utc)
    assert observations[-1].value == Decimal("-25.0000")


def test_fed_assets_transform_calculates_90_day_change() -> None:
    observations = FredTransformAdapter().normalize(
        (FIXTURES / "fred_walcl_history.json").read_bytes(),
        SeriesRequest("fed_assets_90d_change", "WALCL", "percent"),
        transform="change_90d",
        fetched_at=NOW,
    )

    assert observations[-1].value == Decimal("-5.0000")


def test_fx_transform_calculates_signed_30_day_change() -> None:
    payload = json.dumps(
        {
            "observations": [
                {"date": "2026-06-01", "realtime_start": "2026-06-01", "value": "100"},
                {"date": "2026-07-01", "realtime_start": "2026-07-01", "value": "112"},
            ]
        }
    ).encode()
    observations = FredTransformAdapter().normalize(
        payload,
        SeriesRequest("japan_fx_30d_change", "DEXJPUS", "percent"),
        transform="change_30d",
        fetched_at=NOW,
    )

    assert observations[-1].value == Decimal("12.0000")


def test_change_transform_skips_undefined_zero_base_but_keeps_later_history() -> None:
    payload = json.dumps(
        {
            "observations": [
                {"date": "2026-01-01", "realtime_start": "2026-01-01", "value": "0"},
                {"date": "2026-04-01", "realtime_start": "2026-04-01", "value": "2"},
                {"date": "2026-07-01", "realtime_start": "2026-07-01", "value": "4"},
            ]
        }
    ).encode()
    observations = FredTransformAdapter().normalize(
        payload,
        SeriesRequest("gas_90d_change", "GAS", "percent"),
        transform="change_90d",
        fetched_at=NOW,
    )

    assert [item.observed_at.date().isoformat() for item in observations] == ["2026-07-01"]
    assert observations[0].value == Decimal("100.0000")


def test_fred_transform_rejects_unknown_transform() -> None:
    with pytest.raises(SourcePayloadError, match="unsupported"):
        FredTransformAdapter().normalize(
            (FIXTURES / "fred_sp500_history.json").read_bytes(),
            SeriesRequest("derived", "SP500", "percent"),
            transform="unknown",
            fetched_at=NOW,
        )
