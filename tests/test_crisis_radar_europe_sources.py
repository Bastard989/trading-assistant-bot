import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.ecb import EcbAdapter
from trading_bot.crisis_radar.sources.europe_clients import EcbClient, EuropeSourceError, EurostatClient
from trading_bot.crisis_radar.sources.eurostat import EurostatAdapter
from trading_bot.db import Database


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def test_ecb_adapter_parses_ciss_csv() -> None:
    observations = EcbAdapter().normalize_ciss(
        (FIXTURES / "ecb_ciss.csv").read_bytes(), fetched_at=NOW
    )

    assert [item.value for item in observations] == [Decimal("0.08"), Decimal("0.22")]
    assert observations[-1].observed_at == datetime(2026, 7, 17, tzinfo=timezone.utc)
    assert observations[-1].unit == "index"


def test_eurostat_adapter_decodes_json_stat_time_positions() -> None:
    observations = EurostatAdapter().normalize_real_gdp(
        (FIXTURES / "eurostat_gdp.json").read_bytes(), fetched_at=NOW
    )

    assert [item.value for item in observations] == [Decimal("0.3"), Decimal("0.2"), Decimal("0.0")]
    assert observations[-1].observed_at == datetime(2026, 3, 31, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("adapter", "method"),
    [(EcbAdapter(), "normalize_ciss"), (EurostatAdapter(), "normalize_real_gdp")],
)
def test_europe_adapters_reject_malformed_payload(adapter, method) -> None:
    with pytest.raises(SourcePayloadError):
        getattr(adapter, method)(b"{}", fetched_at=NOW)


def test_europe_client_retries_without_credentials() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"payload")

    async def no_sleep(delay: float) -> None:
        assert delay == 0

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await EcbClient(client=client, sleep=no_sleep).fetch_ciss(as_of=NOW)

    assert asyncio.run(scenario()) == b"payload"
    assert calls == 2


def test_europe_client_sanitizes_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(EuropeSourceError, match="HTTP 404"):
                await EurostatClient(client=client).fetch_real_gdp(as_of=NOW)

    asyncio.run(scenario())


class StubEcbClient:
    async def fetch_ciss(self, *, as_of: datetime) -> bytes:
        return (FIXTURES / "ecb_ciss.csv").read_bytes()


class StubEurostatClient:
    async def fetch_real_gdp(self, *, as_of: datetime) -> bytes:
        return (FIXTURES / "eurostat_gdp.json").read_bytes()


def test_europe_sync_builds_starter_v3_snapshot(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "europe.sqlite3")))

    ecb = asyncio.run(service.sync_ecb(StubEcbClient(), fetched_at=NOW))
    eurostat = asyncio.run(service.sync_eurostat(StubEurostatClient(), fetched_at=NOW))

    assert ecb["status"] == "succeeded"
    assert eurostat["status"] == "succeeded"
    assert eurostat["stage"] == "tension"
    overview = service.overview(locale="en")
    assert overview["methodology"]["version"] == "starter-v8"
    assert {item["code"] for item in overview["indicators"]} == {
        "euro_ciss",
        "euro_real_gdp_qoq",
    }
    assert overview["breadth"]["warning_or_worse"] == 1
    ecb_state = next(item for item in overview["indicators"] if item["code"] == "euro_ciss")
    assert ecb_state["raw_band"] == "warning"
    assert ecb_state["band"] == "normal"
    assert ecb_state["persistence_count"] == 1
