import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from trading_bot.crisis_radar.canary import collect_database_metrics
from trading_bot.crisis_radar.catalog import (
    METHODOLOGY_V16_VERSION,
    METHODOLOGY_V17_VERSION,
    OECD_LABOUR_V17_CANDIDATE_INDICATORS,
    V16_INDICATORS,
    V16_SCENARIOS,
    V17_INDICATORS,
    V17_SCENARIOS,
    bootstrap_v16_catalog,
    bootstrap_v17_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.global_clients import OecdClient
from trading_bot.crisis_radar.sources.global_data import OecdAdapter
from trading_bot.crisis_radar.stage_v2 import DEPENDENCY_GRAPH_V17_VERSION
from trading_bot.db import Database


NOW = datetime(2026, 8, 14, 1, tzinfo=timezone.utc)
AREAS = ("CAN", "GBR", "JPN", "KOR", "MEX")
HEADER = (
    "DATAFLOW,REF_AREA,FREQ,MEASURE,UNIT_MEASURE,ACTIVITY,ADJUSTMENT,"
    "TRANSFORMATION,TIME_PERIOD,OBS_VALUE,OBS_STATUS,UNIT_MULT,DECIMALS,BASE_PER"
)


def _months() -> list[str]:
    result = []
    year, month = 2025, 5
    for _ in range(15):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def labour_payload(*, missing_area: str = "", duplicate: bool = False) -> bytes:
    lines = [HEADER]
    values = [Decimal("4.0")] * 12 + [Decimal("4.3"), Decimal("4.6"), Decimal("4.9")]
    for area_index, area in enumerate(AREAS):
        if area == missing_area:
            continue
        offset = Decimal(area_index) / Decimal("10")
        for period, value in zip(_months(), values, strict=True):
            lines.append(
                "OECD.SDD.STES:DSD_KEI@DF_KEI(4.0),"
                f"{area},M,UNEMP,PT_LF,_T,Y,_Z,{period},{value + offset},A,0,1,"
            )
    if duplicate:
        lines.append(lines[1])
    return ("\n".join(lines) + "\n").encode()


def test_oecd_labour_adapter_calculates_latest_contiguous_momentum() -> None:
    observations = OecdAdapter().normalize_unemployment_momentum(
        labour_payload(), fetched_at=NOW
    )

    assert len(observations) == 5
    assert {item.value for item in observations} == {Decimal("0.6000")}
    assert {item.unit for item in observations} == {"percentage_points"}
    assert {item.source_code for item in observations} == {"oecd"}
    assert {item.observed_at.isoformat() for item in observations} == {
        "2026-07-31T00:00:00+00:00"
    }
    assert all(item.released_at == item.fetched_at == NOW for item in observations)
    assert all("release_time_estimated" in {flag.value for flag in item.quality_flags}
               for item in observations)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda payload: payload.replace(b",A,0,1,", b",P,0,1,", 1), "dimensions or status"),
        (lambda payload: payload.replace(b",4.0,A,", b",NaN,A,", 1), "valid range"),
        (lambda payload: payload.replace(b",4.0,A,", b",101,A,", 1), "valid range"),
    ),
)
def test_oecd_labour_adapter_rejects_schema_or_value_drift(mutation, message) -> None:
    with pytest.raises(SourcePayloadError, match=message):
        OecdAdapter().normalize_unemployment_momentum(
            mutation(labour_payload()), fetched_at=NOW
        )


def test_oecd_labour_adapter_rejects_duplicates_missing_regions_and_naive_time() -> None:
    with pytest.raises(SourcePayloadError, match="duplicate"):
        OecdAdapter().normalize_unemployment_momentum(
            labour_payload(duplicate=True), fetched_at=NOW
        )
    with pytest.raises(SourcePayloadError, match="missing for MEX"):
        OecdAdapter().normalize_unemployment_momentum(
            labour_payload(missing_area="MEX"), fetched_at=NOW
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        OecdAdapter().normalize_unemployment_momentum(
            labour_payload(), fetched_at=NOW.replace(tzinfo=None)
        )


def test_oecd_labour_client_pins_dataset_version_and_bounded_dimensions() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=labour_payload())

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OecdClient(client=client).fetch_harmonised_unemployment(
                as_of=NOW
            )

    assert asyncio.run(scenario()) == labour_payload()
    assert len(calls) == 1
    assert calls[0].url.path.endswith(
        "/OECD.SDD.STES,DSD_KEI@DF_KEI,4.0/"
        "CAN+GBR+JPN+KOR+MEX.M.UNEMP.PT_LF._T.Y._Z"
    )
    assert dict(calls[0].url.params) == {
        "startPeriod": "2024-01",
        "endPeriod": "2026-08",
        "dimensionAtObservation": "AllDimensions",
    }


def test_v17_is_disabled_and_preserves_frozen_v16_contract(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v17.sqlite3")
    repository = CrisisRadarRepository(database)
    v16_before = bootstrap_v16_catalog(repository)
    v16_checksum = methodology_checksum(
        version=METHODOLOGY_V16_VERSION,
        indicators=V16_INDICATORS,
        scenarios=V16_SCENARIOS,
    )

    first = bootstrap_v17_catalog(repository)
    second = bootstrap_v17_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V17_VERSION
    assert first["indicator_count"] == len(V17_INDICATORS)
    assert bootstrap_v16_catalog(repository) == v16_before
    assert v16_checksum == (
        "de14e7d4dca74c25f8e90cc3431453b8cce92db05ab6ecf9a4cb6be4cc504e5a"
    )
    assert methodology_checksum(
        version=METHODOLOGY_V16_VERSION,
        indicators=V16_INDICATORS,
        scenarios=V16_SCENARIOS,
    ) == v16_checksum
    assert methodology_checksum(
        version=METHODOLOGY_V17_VERSION,
        indicators=V17_INDICATORS,
        scenarios=V17_SCENARIOS,
    ) != v16_checksum
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT indicator.code, indicator.enabled, threshold.promotion_status,
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
             AND metadata.metadata_version='v17'
            JOIN cr_dependency_assignments AS dependency
              ON dependency.indicator_id=indicator.id
             AND dependency.methodology_id=methodology.id
            WHERE methodology.version=? AND indicator.group_code LIKE '%_labor'
            ORDER BY indicator.code
            """,
            (METHODOLOGY_V17_VERSION,),
        ).fetchall()
    assert len(rows) == 5
    assert {row["code"] for row in rows} == {
        item.code for item in OECD_LABOUR_V17_CANDIDATE_INDICATORS
    }
    assert all(row["enabled"] == 0 for row in rows)
    assert all(row["promotion_status"] == "candidate" for row in rows)
    assert all(row["rationale_payload"] not in {"", "{}"} for row in rows)
    assert all(row["operational_role"] == "regional_labor_confirmation" for row in rows)
    assert all(row["name_ru"] for row in rows)
    assert {row["cluster_code"] for row in rows} == {"labor"}
    assert {row["subchannel_code"] for row in rows} == {
        "canada_labor", "uk_labor", "japan_labor", "korea_labor", "mexico_labor"
    }
    assert {row["graph_version"] for row in rows} == {
        DEPENDENCY_GRAPH_V17_VERSION
    }
    regional = next(item for item in V17_SCENARIOS if item.code == "regional_recession")
    assert set(row["subchannel_code"] for row in rows).issubset(regional.group_codes)


def test_oecd_labour_collection_isolated_from_live_stage_and_required_oecd(tmp_path) -> None:
    class StubClient:
        async def fetch_harmonised_unemployment(self, *, as_of: datetime) -> bytes:
            assert as_of == NOW
            return labour_payload()

    database = Database(tmp_path / "oecd-labour.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    result = asyncio.run(service.sync_oecd_labour(StubClient(), fetched_at=NOW))

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
            WHERE indicator.code LIKE '%_unemployment_momentum'
            """
        ).fetchall()
        snapshots = connection.execute(
            "SELECT count(*) FROM cr_market_snapshots_v2"
        ).fetchone()[0]
    assert [row["enabled"] for row in enabled] == [0]
    assert snapshots == 0
    with pytest.raises(ValueError, match="cannot recompute"):
        asyncio.run(
            service.sync_oecd_labour(
                StubClient(), fetched_at=NOW, recompute_after=True
            )
        )


def test_oecd_labour_failure_is_research_health_not_required_source_failure(tmp_path) -> None:
    class BrokenClient:
        async def fetch_harmonised_unemployment(self, *, as_of: datetime) -> bytes:
            return b"not,csv\n"

    database = Database(tmp_path / "oecd-labour-failure.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    result = asyncio.run(service.sync_oecd_labour(BrokenClient(), fetched_at=NOW))
    assert result["status"] == "failed"
    health = service.source_health(locale="en", as_of=NOW)
    required = next(item for item in health["sources"] if item["code"] == "oecd")
    research = next(
        item for item in health["sources"] if item["code"] == "oecd_labour_research"
    )
    assert required["status"] == "never_synced"
    assert research["status"] == "failed"
    assert research["access_type"] == "research_candidate"
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    metrics = collect_database_metrics(
        database.path, backup_directory=backup_directory, now=NOW
    )
    assert metrics["source_failures"] == 0
    assert metrics["research_source_failure_codes"] == ["oecd_labour_research"]
