import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.bea import BeaAdapter
from trading_bot.crisis_radar.sources.eia import EiaAdapter
from trading_bot.crisis_radar.sources.official_clients import BeaClient, EiaClient, OfficialSourceError
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def test_bea_adapter_filters_real_gdp_and_parses_quarters() -> None:
    observations = BeaAdapter().normalize_real_gdp(
        (FIXTURES / "bea_real_gdp.json").read_bytes(), fetched_at=NOW
    )

    assert [item.value for item in observations] == [Decimal("0.5"), Decimal("2.1")]
    assert observations[-1].observed_at == datetime(2026, 3, 31, tzinfo=timezone.utc)
    assert observations[-1].unit == "percent_annualized"
    assert observations[-1].vintage.startswith("2026-07-20:")


def test_eia_adapter_calculates_90_day_change_without_future_data() -> None:
    observations = EiaAdapter().normalize_wti_90d_change(
        (FIXTURES / "eia_wti.json").read_bytes(), fetched_at=NOW
    )

    assert [item.observed_at.date().isoformat() for item in observations] == ["2026-07-09", "2026-07-19"]
    assert [str(item.value) for item in observations] == ["20.0000", "36.3636"]


@pytest.mark.parametrize(
    ("adapter", "method", "payload"),
    [
        (BeaAdapter(), "normalize_real_gdp", b"{}"),
        (EiaAdapter(), "normalize_wti_90d_change", b"{}"),
    ],
)
def test_official_adapters_reject_malformed_payload(adapter, method, payload) -> None:
    with pytest.raises(SourcePayloadError):
        getattr(adapter, method)(payload, fetched_at=NOW)


def test_official_clients_do_not_expose_keys_and_retry() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert "secret-key" in str(request.url)
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    async def no_sleep(delay: float) -> None:
        assert delay == 1

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await BeaClient("secret-key", client=client, sleep=no_sleep).fetch_real_gdp(as_of=NOW)

    assert b'"ok":true' in asyncio.run(scenario()).replace(b" ", b"")
    assert calls == 2


def test_official_client_sanitizes_http_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(OfficialSourceError, match="HTTP 401") as error:
                await EiaClient("secret-key", client=client).fetch_wti(start_date="2026-01-01")
            assert "secret-key" not in str(error.value)

    asyncio.run(scenario())


class StubBeaClient:
    async def fetch_real_gdp(self, *, as_of: datetime) -> bytes:
        return (FIXTURES / "bea_real_gdp.json").read_bytes()


class StubEiaClient:
    async def fetch_wti(self, *, start_date: str) -> bytes:
        return (FIXTURES / "eia_wti.json").read_bytes()


def test_bea_and_eia_sync_extend_starter_v2_snapshot(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "official.sqlite3")))

    bea = asyncio.run(service.sync_bea(StubBeaClient(), fetched_at=NOW))
    eia = asyncio.run(service.sync_eia(StubEiaClient(), fetched_at=NOW))

    assert bea["status"] == "succeeded"
    assert bea["rows_written"] == 2
    assert eia["status"] == "succeeded"
    assert eia["rows_written"] == 2
    assert eia["stage"] == "tension"
    overview = service.overview(locale="en")
    assert overview["methodology"]["version"] == "starter-v8"
    assert {item["code"] for item in overview["indicators"]} == {
        "us_real_gdp_qoq",
        "wti_90d_change",
    }
    source_status = {source["code"]: source["status"] for source in overview["sources"]}
    assert source_status["bea"] == "succeeded"
    assert source_status["eia"] == "succeeded"
    assert source_status["fred"] is None
