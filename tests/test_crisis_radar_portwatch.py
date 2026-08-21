import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from trading_bot.crisis_radar.canary import collect_database_metrics
from trading_bot.crisis_radar.catalog import (
    METHODOLOGY_V17_VERSION,
    METHODOLOGY_V18_VERSION,
    PORTWATCH_V18_CANDIDATE_INDICATORS,
    V17_INDICATORS,
    V17_SCENARIOS,
    V18_INDICATORS,
    V18_SCENARIOS,
    bootstrap_v17_catalog,
    bootstrap_v18_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.portwatch import (
    PORTWATCH_CHOKEPOINTS,
    PORTWATCH_QUERY_URL,
    PortWatchAdapter,
    PortWatchClient,
    PortWatchSourceError,
)
from trading_bot.crisis_radar.stage_v2 import DEPENDENCY_GRAPH_V18_VERSION
from trading_bot.db import Database


NOW = datetime(2026, 8, 14, 18, tzinfo=timezone.utc)


def portwatch_payload(
    port_id: str = "chokepoint1",
    *,
    current_count: int = 50,
    baseline_count: int = 100,
) -> bytes:
    spec = next(item for item in PORTWATCH_CHOKEPOINTS if item.port_id == port_id)
    latest = NOW.date() - timedelta(days=5)
    first = latest - timedelta(days=371)
    features = []
    for index in range(372):
        observed_on = first + timedelta(days=index)
        count = baseline_count if index < 365 else current_count
        features.append(
            {
                "attributes": {
                    "date": observed_on.isoformat(),
                    "portid": spec.port_id,
                    "portname": spec.port_name,
                    "n_total": count,
                }
            }
        )
    document = {
        "objectIdFieldName": "ObjectId",
        "uniqueIdField": {"name": "ObjectId", "isSystemMaintained": True},
        "globalIdFieldName": "",
        "fields": [
            {"name": "date", "type": "esriFieldTypeDateOnly"},
            {"name": "portid", "type": "esriFieldTypeString"},
            {"name": "portname", "type": "esriFieldTypeString"},
            {"name": "n_total", "type": "esriFieldTypeInteger"},
        ],
        "features": features,
    }
    return json.dumps(document, separators=(",", ":")).encode()


def test_portwatch_adapter_calculates_causal_seven_day_shortfall() -> None:
    observation = PortWatchAdapter().normalize_latest(
        portwatch_payload(), port_id="chokepoint1", fetched_at=NOW
    )

    assert observation.indicator_code == "suez_transit_shortfall"
    assert observation.source_code == "imf_portwatch"
    assert observation.value == Decimal("50.0000")
    assert observation.unit == "percent_shortfall"
    assert observation.observed_at == datetime(2026, 8, 9, tzinfo=timezone.utc)
    assert observation.released_at == observation.fetched_at == NOW
    assert {flag.value for flag in observation.quality_flags} == {
        "release_time_estimated",
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda doc: doc.update({"unexpected": True}), "schema"),
        (lambda doc: doc.update({"exceededTransferLimit": True}), "truncated"),
        (
            lambda doc: doc["fields"][3].update({"type": "esriFieldTypeDouble"}),
            "field contract",
        ),
        (
            lambda doc: doc["features"][0]["attributes"].update(
                {"portname": "Wrong Canal"}
            ),
            "identity",
        ),
        (
            lambda doc: doc["features"][1]["attributes"].update(
                {"date": doc["features"][0]["attributes"]["date"]}
            ),
            "duplicate",
        ),
        (
            lambda doc: doc["features"][200]["attributes"].update(
                {"date": "2025-02-01"}
            ),
            "ordered|contiguous|duplicate",
        ),
        (
            lambda doc: doc["features"][-1]["attributes"].update(
                {"date": "2026-08-15"}
            ),
            "future",
        ),
        (
            lambda doc: doc["features"][0]["attributes"].update({"n_total": 1.5}),
            "transit count",
        ),
    ),
)
def test_portwatch_adapter_rejects_schema_and_data_drift(mutate, message) -> None:
    document = json.loads(portwatch_payload())
    mutate(document)
    with pytest.raises(SourcePayloadError, match=message):
        PortWatchAdapter().normalize_latest(
            json.dumps(document).encode(), port_id="chokepoint1", fetched_at=NOW
        )


def test_portwatch_adapter_rejects_short_or_zero_baseline_and_naive_time() -> None:
    document = json.loads(portwatch_payload())
    document["features"] = document["features"][:-1]
    with pytest.raises(SourcePayloadError, match="row count"):
        PortWatchAdapter().normalize_latest(
            json.dumps(document).encode(), port_id="chokepoint1", fetched_at=NOW
        )
    with pytest.raises(SourcePayloadError, match="baseline"):
        PortWatchAdapter().normalize_latest(
            portwatch_payload(baseline_count=0),
            port_id="chokepoint1",
            fetched_at=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        PortWatchAdapter().normalize_latest(
            portwatch_payload(),
            port_id="chokepoint1",
            fetched_at=NOW.replace(tzinfo=None),
        )


def test_portwatch_client_uses_allowlisted_bounded_query() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=portwatch_payload())

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await PortWatchClient(client=client).fetch_chokepoint(
                "chokepoint1", as_of=NOW
            )

    assert asyncio.run(scenario()) == portwatch_payload()
    assert len(calls) == 1
    assert str(calls[0].url).startswith(PORTWATCH_QUERY_URL)
    assert dict(calls[0].url.params) == {
        "where": (
            "portid='chokepoint1' AND date >= DATE '2025-06-20' "
            "AND date <= DATE '2026-08-14'"
        ),
        "outFields": "date,portid,portname,n_total",
        "returnGeometry": "false",
        "orderByFields": "date ASC",
        "resultRecordCount": "500",
        "f": "json",
    }
    with pytest.raises(ValueError, match="unsupported"):
        asyncio.run(PortWatchClient().fetch_chokepoint("chokepoint999", as_of=NOW))


def test_portwatch_client_retries_transient_status_and_rejects_unsafe_bounds() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (
            httpx.Response(429, headers={"Retry-After": "0"})
            if calls == 1
            else httpx.Response(200, content=portwatch_payload())
        )

    async def no_sleep(delay: float) -> None:
        assert delay == 0

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await PortWatchClient(
                client=client, attempts=2, sleep=no_sleep
            ).fetch_chokepoint("chokepoint1", as_of=NOW)

    assert asyncio.run(scenario()) == portwatch_payload()
    assert calls == 2
    with pytest.raises(ValueError, match="timeout"):
        PortWatchClient(timeout_seconds=21)
    with pytest.raises(ValueError, match="response-size"):
        PortWatchClient(max_response_bytes=512_001)


def test_portwatch_client_and_adapter_reject_oversized_payload() -> None:
    payload = b"x" * 512_001

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await PortWatchClient(client=client).fetch_chokepoint(
                "chokepoint1", as_of=NOW
            )

    with pytest.raises(PortWatchSourceError, match="size limit"):
        asyncio.run(scenario())
    with pytest.raises(SourcePayloadError, match="size limit"):
        PortWatchAdapter().normalize_latest(
            payload, port_id="chokepoint1", fetched_at=NOW
        )


def test_v18_is_disabled_and_preserves_frozen_v17_contract(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v18.sqlite3")
    repository = CrisisRadarRepository(database)
    v17_before = bootstrap_v17_catalog(repository)
    v17_checksum = methodology_checksum(
        version=METHODOLOGY_V17_VERSION,
        indicators=V17_INDICATORS,
        scenarios=V17_SCENARIOS,
    )

    first = bootstrap_v18_catalog(repository)
    second = bootstrap_v18_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V18_VERSION
    assert first["indicator_count"] == len(V18_INDICATORS)
    assert bootstrap_v17_catalog(repository) == v17_before
    assert v17_checksum == (
        "86739aa4518a8bb1733d9532cd37763de5ac9df3804c56356f1d16d66a9676dd"
    )
    assert methodology_checksum(
        version=METHODOLOGY_V17_VERSION,
        indicators=V17_INDICATORS,
        scenarios=V17_SCENARIOS,
    ) == v17_checksum
    assert methodology_checksum(
        version=METHODOLOGY_V18_VERSION,
        indicators=V18_INDICATORS,
        scenarios=V18_SCENARIOS,
    ) != v17_checksum
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT indicator.code, indicator.enabled,
                   threshold.rationale_payload, threshold.operational_role,
                   metadata.name_ru, dependency.cluster_code,
                   dependency.subchannel_code, dependency.graph_version
            FROM cr_indicator_definitions AS indicator
            JOIN cr_threshold_sets AS threshold ON threshold.indicator_id=indicator.id
            JOIN cr_methodology_versions AS methodology
              ON methodology.id=threshold.methodology_id
            JOIN cr_entity_metadata AS metadata
              ON metadata.entity_type='indicator'
             AND metadata.entity_code=indicator.code
             AND metadata.metadata_version='v18'
            JOIN cr_dependency_assignments AS dependency
              ON dependency.indicator_id=indicator.id
             AND dependency.methodology_id=methodology.id
            WHERE methodology.version=? AND indicator.source_id=(
                SELECT id FROM cr_sources WHERE code='imf_portwatch'
            )
            ORDER BY indicator.code
            """,
            (METHODOLOGY_V18_VERSION,),
        ).fetchall()
    assert len(rows) == 5
    assert {row["code"] for row in rows} == {
        item.code for item in PORTWATCH_V18_CANDIDATE_INDICATORS
    }
    assert all(row["enabled"] == 0 for row in rows)
    assert all(row["rationale_payload"] not in {"", "{}"} for row in rows)
    assert all(row["operational_role"] == "shipping_disruption_confirmation" for row in rows)
    assert all(row["name_ru"] for row in rows)
    assert {row["cluster_code"] for row in rows} == {"shipping_logistics"}
    assert {row["subchannel_code"] for row in rows} == {
        item.group_code for item in PORTWATCH_V18_CANDIDATE_INDICATORS
    }
    assert {row["graph_version"] for row in rows} == {DEPENDENCY_GRAPH_V18_VERSION}
    commodity = next(item for item in V18_SCENARIOS if item.code == "commodity_supply_shock")
    assert {item.group_code for item in PORTWATCH_V18_CANDIDATE_INDICATORS}.issubset(
        commodity.group_codes
    )


def test_portwatch_collection_isolated_from_live_stage(tmp_path) -> None:
    class StubClient:
        async def fetch_chokepoint(self, port_id: str, *, as_of: datetime) -> bytes:
            assert as_of == NOW
            return portwatch_payload(port_id)

    database = Database(tmp_path / "portwatch.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )
    result = asyncio.run(service.sync_portwatch(StubClient(), fetched_at=NOW))

    assert result["status"] == "succeeded"
    assert result["rows_fetched"] == result["rows_written"] == 5
    assert result["stage"] is None
    with database.connect() as connection:
        enabled = connection.execute(
            """
            SELECT DISTINCT indicator.enabled
            FROM cr_observations AS observation
            JOIN cr_indicator_definitions AS indicator
              ON indicator.id=observation.indicator_id
            JOIN cr_sources AS source ON source.id=indicator.source_id
            WHERE source.code='imf_portwatch'
            """
        ).fetchall()
        snapshots = connection.execute(
            "SELECT count(*) FROM cr_market_snapshots_v2"
        ).fetchone()[0]
    assert [row["enabled"] for row in enabled] == [0]
    assert snapshots == 0
    with pytest.raises(ValueError, match="cannot recompute"):
        asyncio.run(
            service.sync_portwatch(
                StubClient(), fetched_at=NOW, recompute_after=True
            )
        )


def test_portwatch_failure_is_research_health_and_partial_is_visible(tmp_path) -> None:
    class BrokenClient:
        async def fetch_chokepoint(self, port_id: str, *, as_of: datetime) -> bytes:
            if port_id == "chokepoint1":
                return portwatch_payload(port_id)
            raise PortWatchSourceError("offline")

    database = Database(tmp_path / "portwatch-failure.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )
    result = asyncio.run(service.sync_portwatch(BrokenClient(), fetched_at=NOW))
    assert result["status"] == "partial"
    assert result["rows_fetched"] == result["rows_written"] == 1
    health = service.source_health(locale="en", as_of=NOW)
    research = next(item for item in health["sources"] if item["code"] == "imf_portwatch")
    assert research["status"] == "degraded"
    assert research["access_type"] == "research_candidate"
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    metrics = collect_database_metrics(
        database.path, backup_directory=backup_directory, now=NOW
    )
    assert metrics["source_failures"] == 0
    assert metrics["research_source_failure_codes"] == ["imf_portwatch"]
