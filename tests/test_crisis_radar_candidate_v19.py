import asyncio
import json
from datetime import date, datetime, timezone

from trading_bot.crisis_radar.catalog import (
    FRED_V19_CANDIDATE_INDICATORS,
    METHODOLOGY_V18_VERSION,
    METHODOLOGY_V19_VERSION,
    V18_INDICATORS,
    V18_SCENARIOS,
    V19_INDICATORS,
    V19_SCENARIOS,
    bootstrap_v18_catalog,
    bootstrap_v19_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.stage_v2 import DEPENDENCY_GRAPH_V19_VERSION
from trading_bot.db import Database


NOW = datetime(2026, 8, 21, 13, 15, tzinfo=timezone.utc)


def fred_payload(series_id: str) -> bytes:
    if series_id == "WRESBAL":
        rows = [
            {"date": "2026-01-01", "realtime_start": "2026-01-02", "value": "100"},
            {"date": "2026-04-15", "realtime_start": "2026-04-16", "value": "80"},
        ]
    else:
        rows = [
            {"date": "2026-08-20", "realtime_start": "2026-08-21", "value": "1.5"}
        ]
    return json.dumps({"observations": rows}, separators=(",", ":")).encode()


def test_v19_is_disabled_and_preserves_frozen_v18_contract(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v19.sqlite3")
    repository = CrisisRadarRepository(database)
    v18_before = bootstrap_v18_catalog(repository)
    v18_checksum = methodology_checksum(
        version=METHODOLOGY_V18_VERSION,
        indicators=V18_INDICATORS,
        scenarios=V18_SCENARIOS,
    )

    first = bootstrap_v19_catalog(repository)
    second = bootstrap_v19_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V19_VERSION
    assert first["indicator_count"] == len(V19_INDICATORS)
    assert bootstrap_v18_catalog(repository) == v18_before
    assert v18_checksum == "28185bf0c6eaffb8c0d65992e86edea33aa7f74e902d74876413d755f2544369"
    assert methodology_checksum(
        version=METHODOLOGY_V19_VERSION,
        indicators=V19_INDICATORS,
        scenarios=V19_SCENARIOS,
    ) == "edbfb44cbc9b140166db6753720f4f7195fac675effdae95188a333f14482b53"
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
             AND metadata.metadata_version='v19'
            JOIN cr_dependency_assignments AS dependency
              ON dependency.indicator_id=indicator.id
             AND dependency.methodology_id=methodology.id
            WHERE methodology.version=? AND indicator.code IN (?, ?)
            ORDER BY indicator.code
            """,
            (
                METHODOLOGY_V19_VERSION,
                "us_cpff_spread",
                "us_reserve_balances_90d_change",
            ),
        ).fetchall()
    assert [row["code"] for row in rows] == [
        "us_cpff_spread",
        "us_reserve_balances_90d_change",
    ]
    assert all(row["enabled"] == 0 for row in rows)
    assert all(row["rationale_payload"] not in {"", "{}"} for row in rows)
    assert {row["operational_role"] for row in rows} == {
        "money_market_funding_price",
        "reserve_liquidity_quantity",
    }
    assert all(row["name_ru"] for row in rows)
    assert {row["cluster_code"] for row in rows} == {"dollar_liquidity_banks"}
    assert {row["subchannel_code"] for row in rows} == {
        "money_market_funding_spread",
        "reserve_balances",
    }
    assert {row["graph_version"] for row in rows} == {DEPENDENCY_GRAPH_V19_VERSION}


def test_v19_fred_collection_is_causal_and_does_not_recompute_live_stage(tmp_path) -> None:
    class StubClient:
        async def fetch_history(
            self,
            request,
            *,
            observation_start: date,
            observation_end: date,
            initial_release: bool,
        ) -> bytes:
            assert observation_start == date(2026, 1, 1)
            assert observation_end == date(2026, 8, 21)
            assert initial_release is True
            return fred_payload(request.provider_series_id)

    database = Database(tmp_path / "candidate-v19-collection.sqlite3")
    service = CrisisRadarService(
        CrisisRadarRepository(database),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )
    result = asyncio.run(
        service.backfill_fred(
            StubClient(),
            started_on=date(2026, 1, 1),
            ended_on=date(2026, 8, 21),
            fetched_at=NOW,
            recompute_after=False,
            indicator_codes={item.code for item in FRED_V19_CANDIDATE_INDICATORS},
        )
    )

    assert result["status"] == "succeeded"
    assert result["rows_fetched"] == result["rows_written"] == 2
    assert result["stage"] is None
    with database.connect() as connection:
        observations = connection.execute(
            """
            SELECT indicator.code, observation.value_text, observation.quality_flags
            FROM cr_observations AS observation
            JOIN cr_indicator_definitions AS indicator
              ON indicator.id=observation.indicator_id
            WHERE indicator.code IN (?, ?)
            ORDER BY indicator.code
            """,
            ("us_cpff_spread", "us_reserve_balances_90d_change"),
        ).fetchall()
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM cr_market_snapshots_v2"
        ).fetchone()[0]
    assert [(row["code"], row["value_text"]) for row in observations] == [
        ("us_cpff_spread", "1.5"),
        ("us_reserve_balances_90d_change", "-20.0000"),
    ]
    assert all(row["quality_flags"] == "[]" for row in observations)
    assert snapshot_count == 0
