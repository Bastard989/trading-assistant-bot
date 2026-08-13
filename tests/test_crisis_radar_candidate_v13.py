from datetime import datetime, timezone
from decimal import Decimal

from scripts.replay_crisis_radar_v11 import execute_v13_comparison
from trading_bot.crisis_radar.catalog import (
    FRED_V12_CANDIDATE_INDICATORS,
    METHODOLOGY_V12_VERSION,
    METHODOLOGY_V13_VERSION,
    V12_INDICATORS,
    V12_SCENARIOS,
    V13_INDICATORS,
    V13_SCENARIOS,
    bootstrap_v12_catalog,
    bootstrap_v13_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.domain import (
    CoverageStatus,
    DataFreshness,
    IndicatorBand,
    IndicatorState,
    Observation,
)
from trading_bot.crisis_radar.coverage import normalize_region
from trading_bot.crisis_radar.replay_coverage import (
    SCENARIO_REPLAY_COVERAGE_VERSION,
    assess_scenario_replay_coverage,
)
from trading_bot.crisis_radar.replay_v2 import (
    REPLAY_V13_ENGINE_VERSION,
    v11_signals_as_of,
    v12_signals_as_of,
    v13_signals_as_of,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


NOW = datetime(2010, 1, 1, tzinfo=timezone.utc)
FINANCIAL_STRESS = next(
    item for item in V13_SCENARIOS if item.code == "financial_stress"
)
FINANCIAL_INDICATORS = tuple(
    item for item in V13_INDICATORS if item.group_code in FINANCIAL_STRESS.group_codes
)


def _state(seed, freshness: DataFreshness = DataFreshness.FRESH) -> IndicatorState:
    observation = Observation(
        indicator_code=seed.code,
        source_code="fixture",
        value=seed.thresholds.reference,
        unit=seed.unit,
        observed_at=NOW,
        released_at=NOW,
        fetched_at=NOW,
        vintage="fixture",
    )
    return IndicatorState(
        indicator_code=seed.code,
        group_code=seed.group_code,
        band=IndicatorBand.NORMAL,
        stress_score=Decimal("0"),
        distance_to_next=Decimal("1"),
        freshness=freshness,
        value=observation.value,
        unit=seed.unit,
        snapshot_at=NOW,
        observation=observation,
    )


def _save_scenario_observations(
    database: Database,
    repository: CrisisRadarRepository,
) -> None:
    with database.connect() as connection:
        source_by_code = {
            row[0]: row[1]
            for row in connection.execute(
                """
                SELECT indicator.code, source.code
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id=indicator.source_id
                """
            )
        }
    for seed in FINANCIAL_INDICATORS:
        repository.save_observation(
            Observation(
                indicator_code=seed.code,
                source_code=source_by_code[seed.code],
                value=seed.thresholds.reference,
                unit=seed.unit,
                observed_at=NOW,
                released_at=NOW,
                fetched_at=NOW,
                vintage="initial",
            )
        )


def test_v13_scenario_coverage_requires_ratio_channels_and_regions() -> None:
    healthy = assess_scenario_replay_coverage(
        [_state(seed) for seed in FINANCIAL_INDICATORS],
        indicators=V13_INDICATORS,
        definition=FINANCIAL_STRESS,
    )

    assert healthy.status is CoverageStatus.HEALTHY
    assert healthy.ratio == Decimal("1.0000")
    assert healthy.expected_count == len(FINANCIAL_STRESS.group_codes)
    assert healthy.expected_count < len(FINANCIAL_INDICATORS)
    assert not healthy.missing_channel_classes
    assert not healthy.missing_region_classes
    assert len(healthy.input_checksum) == 64

    missing_emerging = assess_scenario_replay_coverage(
        [
            _state(
                seed,
                DataFreshness.MISSING
                if normalize_region(seed.region_code) in {"CHINA", "IND", "BRA", "MEX"}
                else DataFreshness.FRESH,
            )
            for seed in FINANCIAL_INDICATORS
        ],
        indicators=V13_INDICATORS,
        definition=FINANCIAL_STRESS,
    )
    assert missing_emerging.ratio >= Decimal(".70")
    assert missing_emerging.status is CoverageStatus.INSUFFICIENT_DATA
    assert missing_emerging.missing_region_classes == ("emerging",)

    one_emerging = assess_scenario_replay_coverage(
        [
            _state(
                seed,
                DataFreshness.MISSING
                if normalize_region(seed.region_code) in {"IND", "BRA", "MEX"}
                else DataFreshness.FRESH,
            )
            for seed in FINANCIAL_INDICATORS
        ],
        indicators=V13_INDICATORS,
        definition=FINANCIAL_STRESS,
    )
    assert one_emerging.ratio >= Decimal(".70")
    assert one_emerging.status is CoverageStatus.INSUFFICIENT_DATA
    assert one_emerging.missing_region_classes == ("emerging",)

    missing_credit = assess_scenario_replay_coverage(
        [
            _state(
                seed,
                DataFreshness.MISSING
                if seed.group_code == "credit"
                else DataFreshness.FRESH,
            )
            for seed in FINANCIAL_INDICATORS
        ],
        indicators=V13_INDICATORS,
        definition=FINANCIAL_STRESS,
    )
    assert missing_credit.ratio >= Decimal(".70")
    assert missing_credit.status is CoverageStatus.INSUFFICIENT_DATA
    assert missing_credit.missing_channel_classes == ("credit",)


def test_v13_is_immutable_replay_only_and_preserves_issued_candidates(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v13.sqlite3")
    repository = CrisisRadarRepository(database)
    CrisisRadarService(repository).bootstrap()
    bootstrap_v12_catalog(repository)
    v12_checksum = methodology_checksum(
        version=METHODOLOGY_V12_VERSION,
        indicators=V12_INDICATORS,
        scenarios=V12_SCENARIOS,
    )
    v11_before = v11_signals_as_of(
        repository, scenario_code="financial_stress", snapshot_at=NOW
    )
    v12_before = v12_signals_as_of(
        repository, scenario_code="financial_stress", snapshot_at=NOW
    )

    first = bootstrap_v13_catalog(repository)
    second = bootstrap_v13_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V13_VERSION
    assert first["indicator_count"] == len(V13_INDICATORS)
    assert methodology_checksum(
        version=METHODOLOGY_V12_VERSION,
        indicators=V12_INDICATORS,
        scenarios=V12_SCENARIOS,
    ) == v12_checksum
    assert methodology_checksum(
        version=METHODOLOGY_V13_VERSION,
        indicators=V13_INDICATORS,
        scenarios=V13_SCENARIOS,
    ) != v12_checksum
    assert v11_signals_as_of(
        repository, scenario_code="financial_stress", snapshot_at=NOW
    ) == v11_before
    assert v12_signals_as_of(
        repository, scenario_code="financial_stress", snapshot_at=NOW
    ) == v12_before
    with database.connect() as connection:
        disabled = connection.execute(
            """
            SELECT count(*)
            FROM cr_indicator_definitions
            WHERE code IN ({}) AND enabled=0
            """.format(
                ",".join("?" for _ in FRED_V12_CANDIDATE_INDICATORS)
            ),
            tuple(item.code for item in FRED_V12_CANDIDATE_INDICATORS),
        ).fetchone()[0]
        live_versions = {
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT methodology.version
                FROM cr_market_snapshots_v2 AS snapshot
                JOIN cr_methodology_versions AS methodology
                  ON methodology.id=snapshot.methodology_id
                """
            )
        }
    assert disabled == len(FRED_V12_CANDIDATE_INDICATORS)
    assert METHODOLOGY_V13_VERSION not in live_versions


def test_v13_uses_fixed_scenario_universe_and_reports_global_coverage(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v13-replay.sqlite3")
    repository = CrisisRadarRepository(database)
    CrisisRadarService(repository).bootstrap()
    bootstrap_v13_catalog(repository)
    _save_scenario_observations(database, repository)

    signals = v13_signals_as_of(
        repository,
        scenario_code="financial_stress",
        snapshot_at=NOW,
    )

    assert signals
    assert {item.numeric_coverage for item in signals} == {Decimal("1.0000")}
    assert all(item.backtest_eligible for item in signals)
    assert all(item.market_stage != "insufficient_data" for item in signals)
    assert all(item.global_numeric_coverage < Decimal(".70") for item in signals)
    assert {item.coverage_contract for item in signals} == {
        SCENARIO_REPLAY_COVERAGE_VERSION
    }
    assert {item.input_count for item in signals} == {len(FINANCIAL_INDICATORS)}

    manifest = execute_v13_comparison(
        repository,
        scenario_code="financial_stress",
        started_at=NOW,
        ended_at=NOW,
        cadence_days=30,
        horizon_days=30,
    )
    diagnostics = manifest["candidate_replay_diagnostics"]
    assert manifest["methodology"] == METHODOLOGY_V13_VERSION
    assert manifest["live_probability"] is None
    assert manifest["promotion_gate"]["passed"] is False
    assert diagnostics["eligible_cutoff_count"] == 1
    assert diagnostics["coverage_contract"] == SCENARIO_REPLAY_COVERAGE_VERSION
    assert Decimal(diagnostics["global_numeric_coverage_max"]) < Decimal(".70")
    assert len(manifest["checksums"]["v13_replay"]) == 64
    assert REPLAY_V13_ENGINE_VERSION
