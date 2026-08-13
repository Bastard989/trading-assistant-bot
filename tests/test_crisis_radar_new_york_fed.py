import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.catalog import (
    METHODOLOGY_V14_VERSION,
    METHODOLOGY_V15_VERSION,
    NEW_YORK_FED_V15_CANDIDATE_INDICATORS,
    V14_INDICATORS,
    V14_SCENARIOS,
    V15_INDICATORS,
    V15_SCENARIOS,
    bootstrap_v14_catalog,
    bootstrap_v15_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.new_york_fed import (
    NewYorkFedAdapter,
    NewYorkFedClient,
    NewYorkFedSourceError,
)
from trading_bot.db import Database


FIXTURE = Path(__file__).parent / "fixtures" / "new_york_fed_gscpi.csv"
NOW = datetime(2026, 8, 13, 18, 40, tzinfo=timezone.utc)


def test_gscpi_adapter_validates_vintage_matrix_and_normalizes_latest_only() -> None:
    payload = FIXTURE.read_bytes()
    adapter = NewYorkFedAdapter()

    contract = adapter.inspect_matrix(payload, fetched_at=NOW)
    observation = adapter.normalize_latest(payload, fetched_at=NOW)

    assert contract.vintage_count == 3
    assert contract.observation_count == 5
    assert contract.non_missing_value_count == 12
    assert contract.latest_vintage == "2026-08"
    assert contract.latest_observation_at == datetime(2026, 7, 31, tzinfo=timezone.utc)
    assert contract.latest_value == Decimal("0.79")
    assert observation.indicator_code == "global_supply_chain_pressure"
    assert observation.source_code == "new_york_fed"
    assert observation.value == Decimal("0.7900")
    assert observation.observed_at == contract.latest_observation_at
    assert observation.released_at == NOW
    assert observation.fetched_at == NOW
    assert observation.vintage == "2026-08"
    assert {flag.value for flag in observation.quality_flags} == {
        "release_time_estimated"
    }


@pytest.mark.parametrize(
    ("text", "message"),
    (
        (
            "Date,Aug-26,Aug-26\n31-Jul-2026,0.7,0.8\n30-Jun-2026,0.5,0.6\n",
            "unique and ordered",
        ),
        (
            "Date,Aug-26,Sep-26\n30-Jun-2026,0.5,0.6\n31-Jul-2026,0.7,0.8\n",
            "future",
        ),
        (
            "Date,Jun-26,Aug-26\n31-Jul-2026,0.7,0.8\n30-Jun-2026,0.5,0.6\n",
            "unavailable in that month",
        ),
        (
            "Date,Aug-26\n31-Jul-2026,NaN\n30-Jun-2026,0.6\n",
            "finite",
        ),
    ),
)
def test_gscpi_adapter_rejects_schema_drift_and_causal_leaks(
    text: str, message: str
) -> None:
    with pytest.raises(SourcePayloadError, match=message):
        NewYorkFedAdapter().inspect_matrix(text.encode(), fetched_at=NOW)


def test_gscpi_client_uses_exact_official_endpoint_and_bounded_retries() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == NewYorkFedClient.endpoint
        assert request.headers["Accept"] == "text/csv"
        assert request.headers["User-Agent"] == "TradingAssistant-CrisisRadar/10"
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=FIXTURE.read_bytes())

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await NewYorkFedClient(
                client=client,
                sleep=lambda _seconds: asyncio.sleep(0),
            ).fetch_gscpi()

    assert asyncio.run(scenario()) == FIXTURE.read_bytes()
    assert calls == 2


def test_gscpi_client_rejects_oversized_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100_001)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(NewYorkFedSourceError, match="size limit"):
                await NewYorkFedClient(
                    client=client, max_response_bytes=100_000
                ).fetch_gscpi()

    asyncio.run(scenario())


def test_v15_is_disabled_immutable_and_preserves_v14(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v15.sqlite3")
    repository = CrisisRadarRepository(database)
    v14_before = bootstrap_v14_catalog(repository)
    v14_checksum = methodology_checksum(
        version=METHODOLOGY_V14_VERSION,
        indicators=V14_INDICATORS,
        scenarios=V14_SCENARIOS,
    )

    first = bootstrap_v15_catalog(repository)
    second = bootstrap_v15_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V15_VERSION
    assert first["indicator_count"] == len(V15_INDICATORS)
    assert bootstrap_v14_catalog(repository) == v14_before
    assert methodology_checksum(
        version=METHODOLOGY_V14_VERSION,
        indicators=V14_INDICATORS,
        scenarios=V14_SCENARIOS,
    ) == v14_checksum
    assert methodology_checksum(
        version=METHODOLOGY_V15_VERSION,
        indicators=V15_INDICATORS,
        scenarios=V15_SCENARIOS,
    ) != v14_checksum
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT indicator.enabled, threshold.basis, threshold.promotion_status,
                   threshold.rationale_payload, threshold.source_url,
                   threshold.operational_role, threshold.profile,
                   metadata.name_ru, metadata.description_ru,
                   dependency.cluster_code, dependency.subchannel_code
            FROM cr_indicator_definitions AS indicator
            JOIN cr_threshold_sets AS threshold ON threshold.indicator_id=indicator.id
            JOIN cr_methodology_versions AS methodology
              ON methodology.id=threshold.methodology_id
            JOIN cr_entity_metadata AS metadata
              ON metadata.entity_type='indicator'
             AND metadata.entity_code=indicator.code
             AND metadata.metadata_version='v15'
            JOIN cr_dependency_assignments AS dependency
              ON dependency.indicator_id=indicator.id
             AND dependency.methodology_id=methodology.id
            WHERE methodology.version=? AND indicator.code=?
            """,
            (METHODOLOGY_V15_VERSION, "global_supply_chain_pressure"),
        ).fetchone()
    assert row is not None
    assert row["enabled"] == 0
    assert row["basis"] == "hybrid"
    assert row["promotion_status"] == "candidate"
    assert row["rationale_payload"] not in {"", "{}"}
    assert row["source_url"] == "https://www.newyorkfed.org/research/policy/gscpi"
    assert row["operational_role"] == "global_supply_chain_pressure"
    assert row["profile"] == "macro_monthly"
    assert row["name_ru"] and row["description_ru"]
    assert row["cluster_code"] == "commodities_supply"
    assert row["subchannel_code"] == "supply_chain_pressure"


def test_gscpi_collection_persists_evidence_but_does_not_recompute_live_stage(
    tmp_path,
) -> None:
    class StubClient:
        async def fetch_gscpi(self) -> bytes:
            return FIXTURE.read_bytes()

    database = Database(tmp_path / "gscpi-sync.sqlite3")
    repository = CrisisRadarRepository(database)
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    result = asyncio.run(service.sync_new_york_fed(StubClient(), fetched_at=NOW))

    assert result == {
        "sync_run_id": result["sync_run_id"],
        "status": "succeeded",
        "rows_fetched": 1,
        "rows_written": 1,
        "stage": None,
    }
    with database.connect() as connection:
        observation = connection.execute(
            """
            SELECT observation.value_text, observation.vintage,
                   observation.released_at, indicator.enabled
            FROM cr_observations AS observation
            JOIN cr_indicator_definitions AS indicator
              ON indicator.id=observation.indicator_id
            WHERE indicator.code='global_supply_chain_pressure'
            """
        ).fetchone()
        live_snapshots = connection.execute(
            "SELECT count(*) FROM cr_market_snapshots"
        ).fetchone()[0]
        v2_snapshots = connection.execute(
            "SELECT count(*) FROM cr_market_snapshots_v2"
        ).fetchone()[0]
    assert dict(observation) == {
        "value_text": "0.7900",
        "vintage": "2026-08",
        "released_at": "2026-08-13T18:40:00+00:00",
        "enabled": 0,
    }
    assert live_snapshots == 0
    assert v2_snapshots == 0
    assert len(NEW_YORK_FED_V15_CANDIDATE_INDICATORS) == 1


def test_gscpi_collection_preserves_equal_value_in_a_new_official_vintage(
    tmp_path,
) -> None:
    class StubClient:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        async def fetch_gscpi(self) -> bytes:
            return self.payload

    august = (
        b"Date,Aug-26\n"
        b"30-Jun-2026,1.00\n"
        b"31-Jul-2026,0.79\n"
    )
    september = (
        b"Date,Aug-26,Sep-26\n"
        b"30-Jun-2026,1.00,1.00\n"
        b"31-Jul-2026,0.79,0.79\n"
    )
    database = Database(tmp_path / "gscpi-vintages.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    first = asyncio.run(
        service.sync_new_york_fed(StubClient(august), fetched_at=NOW)
    )
    second = asyncio.run(
        service.sync_new_york_fed(
            StubClient(september),
            fetched_at=datetime(2026, 9, 13, 18, 40, tzinfo=timezone.utc),
        )
    )

    assert first["rows_written"] == 1
    assert second["rows_written"] == 1
    with database.connect() as connection:
        vintages = connection.execute(
            """
            SELECT observation.vintage
            FROM cr_observations AS observation
            JOIN cr_indicator_definitions AS indicator
              ON indicator.id=observation.indicator_id
            WHERE indicator.code='global_supply_chain_pressure'
            ORDER BY observation.vintage
            """
        ).fetchall()
    assert [row["vintage"] for row in vintages] == ["2026-08", "2026-09"]
