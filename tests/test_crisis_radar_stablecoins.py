import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.catalog import (
    METHODOLOGY_V15_VERSION,
    METHODOLOGY_V16_VERSION,
    STABLECOIN_V16_CANDIDATE_INDICATORS,
    V15_INDICATORS,
    V15_SCENARIOS,
    V16_INDICATORS,
    V16_SCENARIOS,
    bootstrap_v15_catalog,
    bootstrap_v16_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.canary import collect_database_metrics
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.bybit import BybitClient
from trading_bot.crisis_radar.sources.stablecoins import (
    BinanceMarketClient,
    BinanceMarketSourceError,
    StablecoinDislocationAdapter,
)
from trading_bot.db import Database


FIXTURES = Path(__file__).parent / "fixtures"
BINANCE_FIXTURE = FIXTURES / "binance_usdc_usdt_book.json"
BYBIT_FIXTURE = FIXTURES / "bybit_usdc_usdt_ticker.json"
NOW = datetime(2026, 8, 13, 21, tzinfo=timezone.utc)


def test_stablecoin_adapter_uses_executable_midpoint_and_spread_contract() -> None:
    adapter = StablecoinDislocationAdapter()

    binance = adapter.normalize_binance(BINANCE_FIXTURE.read_bytes(), fetched_at=NOW)
    bybit = adapter.normalize_bybit(BYBIT_FIXTURE.read_bytes(), fetched_at=NOW)
    spread_payload = json.dumps(
        {
            "symbol": "USDCUSDT",
            "bidPrice": "0.98",
            "bidQty": "1",
            "askPrice": "1.02",
            "askQty": "1",
        }
    ).encode()
    spread = adapter.normalize_binance(spread_payload, fetched_at=NOW)

    assert binance.value == Decimal("0.4500")
    assert binance.source_code == "binance_market"
    assert bybit.value == Decimal("0.4400")
    assert bybit.source_code == "bybit"
    assert spread.value == Decimal("2.0000")
    assert binance.unit == bybit.unit == "percent_from_peg"
    assert binance.released_at == binance.fetched_at == NOW


@pytest.mark.parametrize(
    ("venue", "payload", "message"),
    (
        (
            "binance",
            {"symbol": "BTCUSDT", "bidPrice": "1", "bidQty": "1", "askPrice": "1", "askQty": "1"},
            "symbol",
        ),
        (
            "binance",
            {"symbol": "USDCUSDT", "bidPrice": "1.1", "bidQty": "1", "askPrice": "1", "askQty": "1"},
            "bid exceeds ask",
        ),
        (
            "binance",
            {"symbol": "USDCUSDT", "bidPrice": "1", "bidQty": "0", "askPrice": "1", "askQty": "1"},
            "executable size",
        ),
        (
            "binance",
            {"symbol": "USDCUSDT", "bidPrice": "NaN", "bidQty": "1", "askPrice": "1", "askQty": "1"},
            "finite",
        ),
    ),
)
def test_stablecoin_adapter_rejects_non_executable_or_wrong_market_payloads(
    venue: str, payload: dict, message: str
) -> None:
    encoded = json.dumps(payload).encode()
    with pytest.raises(SourcePayloadError, match=message):
        getattr(StablecoinDislocationAdapter(), f"normalize_{venue}")(
            encoded, fetched_at=NOW
        )


def test_bybit_stablecoin_adapter_rejects_stale_provider_clock_and_naive_fetch() -> None:
    adapter = StablecoinDislocationAdapter()

    with pytest.raises(SourcePayloadError, match="freshness"):
        adapter.normalize_bybit(
            BYBIT_FIXTURE.read_bytes(), fetched_at=NOW + timedelta(minutes=6)
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.normalize_bybit(
            BYBIT_FIXTURE.read_bytes(), fetched_at=NOW.replace(tzinfo=None)
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.normalize_binance(
            BINANCE_FIXTURE.read_bytes(), fetched_at=NOW.replace(tzinfo=None)
        )


def test_binance_client_uses_data_only_host_exact_symbol_and_bounded_retries() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=BINANCE_FIXTURE.read_bytes())

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await BinanceMarketClient(
                client=client,
                sleep=lambda _seconds: asyncio.sleep(0),
            ).fetch_usdc_usdt_book()

    assert asyncio.run(scenario()) == BINANCE_FIXTURE.read_bytes()
    assert len(calls) == 2
    assert str(calls[-1].url).startswith(
        "https://data-api.binance.vision/api/v3/ticker/bookTicker?"
    )
    assert dict(calls[-1].url.params) == {"symbol": "USDCUSDT"}
    assert "X-MBX-APIKEY" not in calls[-1].headers


def test_binance_client_rejects_oversized_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1001)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BinanceMarketSourceError, match="size limit"):
                await BinanceMarketClient(
                    client=client,
                    max_response_bytes=1000,
                ).fetch_usdc_usdt_book()

    asyncio.run(scenario())


def test_bybit_client_uses_exact_public_spot_ticker_contract() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=BYBIT_FIXTURE.read_bytes())

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await BybitClient(client=client).fetch_spot_ticker("USDCUSDT")

    assert asyncio.run(scenario()) == BYBIT_FIXTURE.read_bytes()
    assert calls[0].url.path == "/v5/market/tickers"
    assert dict(calls[0].url.params) == {
        "category": "spot",
        "symbol": "USDCUSDT",
    }
    with pytest.raises(ValueError, match="unsupported"):
        asyncio.run(BybitClient().fetch_spot_ticker("BTCUSDT"))


def test_v16_is_disabled_immutable_and_preserves_v15(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v16.sqlite3")
    repository = CrisisRadarRepository(database)
    v15_before = bootstrap_v15_catalog(repository)
    v15_checksum = methodology_checksum(
        version=METHODOLOGY_V15_VERSION,
        indicators=V15_INDICATORS,
        scenarios=V15_SCENARIOS,
    )

    first = bootstrap_v16_catalog(repository)
    second = bootstrap_v16_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V16_VERSION
    assert first["indicator_count"] == len(V16_INDICATORS)
    assert bootstrap_v15_catalog(repository) == v15_before
    assert methodology_checksum(
        version=METHODOLOGY_V15_VERSION,
        indicators=V15_INDICATORS,
        scenarios=V15_SCENARIOS,
    ) == v15_checksum
    assert methodology_checksum(
        version=METHODOLOGY_V16_VERSION,
        indicators=V16_INDICATORS,
        scenarios=V16_SCENARIOS,
    ) != v15_checksum
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT indicator.code, indicator.enabled, threshold.promotion_status,
                   threshold.rationale_payload, threshold.operational_role,
                   metadata.name_ru, dependency.cluster_code,
                   dependency.subchannel_code
            FROM cr_indicator_definitions AS indicator
            JOIN cr_threshold_sets AS threshold ON threshold.indicator_id=indicator.id
            JOIN cr_methodology_versions AS methodology
              ON methodology.id=threshold.methodology_id
            JOIN cr_entity_metadata AS metadata
              ON metadata.entity_type='indicator'
             AND metadata.entity_code=indicator.code
             AND metadata.metadata_version='v16'
            JOIN cr_dependency_assignments AS dependency
              ON dependency.indicator_id=indicator.id
             AND dependency.methodology_id=methodology.id
            WHERE methodology.version=? AND indicator.group_code='stablecoin_stress'
            ORDER BY indicator.code
            """,
            (METHODOLOGY_V16_VERSION,),
        ).fetchall()
    assert len(rows) == 2
    assert {row["code"] for row in rows} == {
        item.code for item in STABLECOIN_V16_CANDIDATE_INDICATORS
    }
    assert all(row["enabled"] == 0 for row in rows)
    assert all(row["promotion_status"] == "candidate" for row in rows)
    assert all(row["rationale_payload"] not in {"", "{}"} for row in rows)
    assert all(row["operational_role"] for row in rows)
    assert all(row["name_ru"] for row in rows)
    assert {row["cluster_code"] for row in rows} == {"crypto_stablecoins"}
    assert {row["subchannel_code"] for row in rows} == {
        "usdc_usdt_cross_venue"
    }
    exchange = next(
        scenario for scenario in V16_SCENARIOS
        if scenario.code == "exchange_stablecoin_failure"
    )
    assert "stablecoin_stress" in exchange.group_codes
    assert "stablecoin_stress" in exchange.anchor_groups


def test_stablecoin_collection_is_disabled_and_does_not_recompute_live_stage(
    tmp_path,
) -> None:
    class StubClient:
        async def fetch_usdc_usdt_book(self) -> bytes:
            return BINANCE_FIXTURE.read_bytes()

    database = Database(tmp_path / "stablecoin-sync.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    result = asyncio.run(
        service.sync_binance_stablecoin(StubClient(), fetched_at=NOW)
    )

    assert result["status"] == "succeeded"
    assert result["rows_fetched"] == result["rows_written"] == 1
    assert result["stage"] is None
    with database.connect() as connection:
        stored = connection.execute(
            """
            SELECT observation.value_text, indicator.enabled
            FROM cr_observations AS observation
            JOIN cr_indicator_definitions AS indicator
              ON indicator.id=observation.indicator_id
            WHERE indicator.code='usdc_usdt_dislocation_binance'
            """
        ).fetchone()
        snapshots = connection.execute(
            "SELECT count(*) FROM cr_market_snapshots_v2"
        ).fetchone()[0]
    assert dict(stored) == {"value_text": "0.4500", "enabled": 0}
    assert snapshots == 0
    with pytest.raises(ValueError, match="cannot recompute"):
        asyncio.run(
            service.sync_binance_stablecoin(
                StubClient(), fetched_at=NOW, recompute_after=True
            )
        )


def test_bybit_research_failure_does_not_degrade_required_bybit_health(tmp_path) -> None:
    class StubClient:
        async def fetch_spot_ticker(self, symbol: str) -> bytes:
            assert symbol == "USDCUSDT"
            return b"{}"

    database = Database(tmp_path / "bybit-research-degradation.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    result = asyncio.run(
        service.sync_bybit_stablecoin(StubClient(), fetched_at=NOW)
    )

    assert result["status"] == "failed"
    assert result["rows_fetched"] == 0
    with database.connect() as connection:
        run = connection.execute(
            """
            SELECT status, error_code, error_detail
            FROM cr_sync_runs WHERE source_id=(
                SELECT id FROM cr_sources WHERE code='bybit_stablecoin_research'
            ) ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    assert dict(run) == {
        "status": "failed",
        "error_code": "source_error",
        "error_detail": "SourcePayloadError",
    }
    health = service.source_health(locale="en", as_of=NOW)
    bybit = next(item for item in health["sources"] if item["code"] == "bybit")
    research = next(
        item
        for item in health["sources"]
        if item["code"] == "bybit_stablecoin_research"
    )
    assert bybit["status"] == "never_synced"
    assert research["status"] == "failed"
    assert research["access_type"] == "research_candidate"
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    metrics = collect_database_metrics(
        database.path,
        backup_directory=backup_directory,
        now=NOW,
    )
    assert metrics["source_failures"] == 0
    assert metrics["research_source_failure_codes"] == [
        "bybit_stablecoin_research"
    ]


def test_bybit_research_success_writes_disabled_input_without_extra_snapshot(
    tmp_path,
) -> None:
    class StubClient:
        async def fetch_spot_ticker(self, symbol: str) -> bytes:
            assert symbol == "USDCUSDT"
            return BYBIT_FIXTURE.read_bytes()

    database = Database(tmp_path / "bybit-stablecoin-success.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    result = asyncio.run(
        service.sync_bybit_stablecoin(
            StubClient(),
            fetched_at=NOW,
            recompute_after=False,
        )
    )

    assert result["status"] == "succeeded"
    assert result["rows_fetched"] == result["rows_written"] == 1
    with database.connect() as connection:
        stored = connection.execute(
            """
            SELECT observation.value_text, indicator.enabled
            FROM cr_observations AS observation
            JOIN cr_indicator_definitions AS indicator
              ON indicator.id=observation.indicator_id
            WHERE indicator.code='usdc_usdt_dislocation_bybit'
            """
        ).fetchone()
        snapshots = connection.execute(
            "SELECT count(*) FROM cr_market_snapshots_v2"
        ).fetchone()[0]
    assert dict(stored) == {"value_text": "0.4400", "enabled": 0}
    assert snapshots == 0
    with pytest.raises(ValueError, match="cannot recompute"):
        asyncio.run(
            service.sync_bybit_stablecoin(
                StubClient(), fetched_at=NOW, recompute_after=True
            )
        )
