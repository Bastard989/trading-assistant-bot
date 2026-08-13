from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_bot.crisis_radar.catalog import (
    FRED_V12_CANDIDATE_INDICATORS,
    METHODOLOGY_CODE,
    METHODOLOGY_V11_VERSION,
    METHODOLOGY_V12_VERSION,
    V11_INDICATORS,
    V11_SCENARIOS,
    V12_INDICATORS,
    V12_SCENARIOS,
    bootstrap_v11_catalog,
    bootstrap_v12_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.domain import Observation, RiskDirection
from trading_bot.crisis_radar.replay_v2 import (
    REPLAY_V12_ENGINE_VERSION,
    replay_v12_scenario,
    v11_signals_as_of,
    v12_signals_as_of,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.stage_v2 import dependency_for
from trading_bot.db import Database
from scripts.replay_crisis_radar_v11 import execute_v12_comparison


NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


def test_candidate_v12_is_immutable_complete_and_does_not_mutate_v11(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v12.sqlite3")
    repository = CrisisRadarRepository(database)
    v11 = bootstrap_v11_catalog(repository)
    v11_checksum = methodology_checksum(
        version=METHODOLOGY_V11_VERSION,
        indicators=V11_INDICATORS,
        scenarios=V11_SCENARIOS,
    )

    first = bootstrap_v12_catalog(repository)
    second = bootstrap_v12_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V12_VERSION
    assert first["indicator_count"] == len(V12_INDICATORS)
    assert first["methodology_id"] != v11["methodology_id"]
    assert methodology_checksum(
        version=METHODOLOGY_V11_VERSION,
        indicators=V11_INDICATORS,
        scenarios=V11_SCENARIOS,
    ) == v11_checksum
    assert methodology_checksum(
        version=METHODOLOGY_V12_VERSION,
        indicators=V12_INDICATORS,
        scenarios=V12_SCENARIOS,
    ) != v11_checksum

    new_codes = {item.code for item in FRED_V12_CANDIDATE_INDICATORS}
    with database.connect() as connection:
        stored_v11_checksum = connection.execute(
            "SELECT checksum FROM cr_methodology_versions WHERE version=?",
            (METHODOLOGY_V11_VERSION,),
        ).fetchone()[0]
        rows = connection.execute(
            """
            SELECT code, enabled
            FROM cr_indicator_definitions
            WHERE code IN ({})
            """.format(",".join("?" for _ in new_codes)),
            tuple(sorted(new_codes)),
        ).fetchall()
        threshold_contract = connection.execute(
            """
            SELECT count(*), min(length(metadata_checksum)),
                   min(length(rationale_payload)), min(length(source_url)),
                   min(length(operational_role)), min(length(profile)),
                   min(length(promotion_evidence_payload)),
                   min(promotion_status), max(promotion_status)
            FROM cr_threshold_sets AS thresholds
            JOIN cr_methodology_versions AS methodology
              ON methodology.id=thresholds.methodology_id
            WHERE methodology.version=?
            """,
            (METHODOLOGY_V12_VERSION,),
        ).fetchone()
        metadata_count = connection.execute(
            """
            SELECT count(*) FROM cr_entity_metadata
            WHERE metadata_version='v12' AND entity_type='indicator'
            """
        ).fetchone()[0]

    assert stored_v11_checksum == v11_checksum
    assert {row[0] for row in rows} == new_codes
    assert all(row[1] == 0 for row in rows)
    assert threshold_contract[0] == len(V12_INDICATORS)
    assert threshold_contract[1] == 64
    assert all(value > 0 for value in threshold_contract[2:7])
    assert threshold_contract[7:] == ("candidate", "candidate")
    assert metadata_count == len(V12_INDICATORS)


def test_v12_thresholds_directions_and_dependency_channels_are_explicit() -> None:
    by_code = {item.code: item for item in FRED_V12_CANDIDATE_INDICATORS}

    assert by_code["us_initial_claims"].thresholds.warning == Decimal("300000")
    assert by_code["us_unemployment_rate"].thresholds.critical == Decimal("9")
    assert by_code["us_housing_starts_90d_change"].thresholds.direction is (
        RiskDirection.LOWER_IS_WORSE
    )
    assert by_code["brent_90d_change"].thresholds.direction is RiskDirection.TWO_SIDED
    assert by_code["henry_hub_gas_90d_change"].thresholds.critical == Decimal("150")

    assignments = {
        code: dependency_for(
            code=seed.code,
            group_code=seed.group_code,
            region_code=seed.region_code,
        )
        for code, seed in by_code.items()
    }
    assert assignments["us_initial_claims"].subchannel_code == "claims"
    assert assignments["us_cre_delinquency_rate"].subchannel_code == "cre_delinquency"
    assert assignments["us_housing_starts_90d_change"].subchannel_code == "housing_activity"
    assert assignments["fed_liquidity_swaps"].subchannel_code == "central_bank_swap_lines"
    assert assignments["nasdaq_composite_30d_drawdown"].subchannel_code == (
        assignments["nasdaq_100_30d_drawdown"].subchannel_code
    )
    assert assignments["nasdaq_composite_30d_drawdown"].cluster_code == "markets_fx"

    tech = next(item for item in V12_SCENARIOS if item.code == "tech_ai_repricing")
    assert "technology_market" in tech.group_codes
    assert "technology_market" in tech.anchor_groups


def test_v12_replay_can_include_disabled_candidate_without_enabling_live(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v12-replay.sqlite3")
    repository = CrisisRadarRepository(database)
    bootstrap_v11_catalog(repository)
    bootstrap_v12_catalog(repository)
    seed = next(
        item for item in FRED_V12_CANDIDATE_INDICATORS if item.code == "us_initial_claims"
    )
    repository.save_observation(
        Observation(
            indicator_code=seed.code,
            source_code="fred",
            value=Decimal("310000"),
            unit=seed.unit,
            observed_at=NOW,
            released_at=NOW,
            fetched_at=NOW,
            vintage=NOW.date().isoformat(),
        )
    )

    live_inputs = repository.analysis_inputs_as_of(
        METHODOLOGY_CODE,
        METHODOLOGY_V12_VERSION,
        as_of=NOW,
        causal_only=True,
    )
    replay_inputs = repository.analysis_inputs_as_of(
        METHODOLOGY_CODE,
        METHODOLOGY_V12_VERSION,
        as_of=NOW,
        causal_only=True,
        include_disabled=True,
    )

    assert all(item.observation.indicator_code != seed.code for item in live_inputs)
    assert [item.observation.indicator_code for item in replay_inputs] == [seed.code]

    v12_signals = v12_signals_as_of(
        repository,
        scenario_code="regional_recession",
        snapshot_at=NOW,
    )
    v11_signals = v11_signals_as_of(
        repository,
        scenario_code="regional_recession",
        snapshot_at=NOW,
    )
    replay = replay_v12_scenario(
        repository,
        "regional_recession",
        started_at=NOW,
        ended_at=NOW,
        step=timedelta(days=1),
    )

    assert {item.input_count for item in v12_signals} == {1}
    assert all(item.observation_ids for item in v12_signals)
    assert all(not item.backtest_eligible for item in v12_signals)
    assert {item.eligibility_reason for item in v12_signals} == {
        "insufficient_numeric_coverage"
    }
    assert {item.input_count for item in v11_signals} == {0}
    assert replay.methodology_version == METHODOLOGY_V12_VERSION
    assert replay.engine_version == REPLAY_V12_ENGINE_VERSION
    assert replay.signals == v12_signals
    assert replay.checksum
    with database.connect() as connection:
        assert connection.execute(
            "SELECT enabled FROM cr_indicator_definitions WHERE code=?", (seed.code,)
        ).fetchone()[0] == 0


def test_v12_comparison_is_diagnostic_and_never_promotes_sparse_history(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v12-manifest.sqlite3")
    repository = CrisisRadarRepository(database)
    CrisisRadarService(repository).bootstrap()
    bootstrap_v12_catalog(repository)
    seed = next(
        item for item in FRED_V12_CANDIDATE_INDICATORS if item.code == "us_initial_claims"
    )
    cutoff = datetime(2010, 1, 1, tzinfo=timezone.utc)
    repository.save_observation(
        Observation(
            indicator_code=seed.code,
            source_code="fred",
            value=Decimal("310000"),
            unit=seed.unit,
            observed_at=cutoff,
            released_at=cutoff,
            fetched_at=cutoff,
            vintage="initial",
        )
    )

    manifest = execute_v12_comparison(
        repository,
        scenario_code="financial_stress",
        started_at=cutoff,
        ended_at=cutoff,
        cadence_days=30,
        horizon_days=30,
    )

    assert manifest["methodology"] == METHODOLOGY_V12_VERSION
    assert manifest["candidate_status"] == "shadow"
    assert manifest["live_probability"] is None
    assert manifest["promotion_gate"]["passed"] is False
    diagnostics = manifest["candidate_replay_diagnostics"]
    assert diagnostics["cutoff_count"] == 1
    assert diagnostics["eligible_cutoff_count"] == 0
    assert diagnostics["input_count_min"] == diagnostics["input_count_max"] == 1
    assert Decimal("0") < Decimal(diagnostics["numeric_coverage_min"]) < Decimal(".70")
    assert diagnostics["numeric_coverage_max"] == diagnostics["numeric_coverage_min"]
    assert diagnostics["stage_counts"] == {"insufficient_data": 1}
    assert diagnostics["eligibility_reason_counts"] == {
        "insufficient_numeric_coverage": 1
    }
    assert len(manifest["checksums"]["v12_replay"]) == 64
    assert len(manifest["manifest_checksum"]) == 64
