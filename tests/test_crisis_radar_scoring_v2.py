from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_bot.crisis_radar.domain import (
    CoverageStatus,
    DataFreshness,
    IndicatorBand,
    RiskDirection,
)
from trading_bot.crisis_radar.catalog import (
    FRED_V12_RESEARCH_INDICATORS,
    METHODOLOGY_V11_VERSION,
    V11_INDICATORS,
    V11_SCENARIOS,
    bootstrap_v11_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.scoring_v2 import (
    IndicatorAgreement,
    PROFILES,
    SCORING_VARIANTS,
    score_indicator_v2,
    score_variant,
)
from trading_bot.crisis_radar.replay_v2 import replay_v11_scenario, v11_signals_as_of
from trading_bot.crisis_radar.stage_v2 import calculate_stage_v2, dependency_for
from trading_bot.crisis_radar.trends import IndicatorFeatures, WindowFeature
from trading_bot.crisis_radar.domain import Observation
from trading_bot.db import CURRENT_SCHEMA_VERSION, Database
from scripts.replay_crisis_radar_v11 import execute_v11_comparison


NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def _features(
    code: str,
    *,
    percentile: str = ".99",
    zscore: str = "3",
    persistence: int = 8,
    change: str = "20",
) -> IndicatorFeatures:
    window = WindowFeature(
        change=Decimal(change),
        percent_change=Decimal(change),
        robust_slope_per_day=Decimal(".5"),
        acceleration=Decimal(".5"),
        observation_count=30,
    )
    return IndicatorFeatures(
        indicator_code=code,
        snapshot_at=NOW,
        windows={name: window for name in ("7d", "30d", "90d", "6m", "12m")},
        percentile=Decimal(percentile),
        mad_zscore=Decimal(zscore),
        volatility=Decimal("1"),
        volatility_regime="extreme",
        change_point=True,
        persistence_count=persistence,
        worsening_score=Decimal("1"),
        state_machine=None,
        input_checksum=(code[0] * 64),
    )


def test_profiles_are_versioned_by_frequency_and_sum_to_one() -> None:
    assert PROFILES["market_daily"].minimum_history == 252
    assert PROFILES["macro_monthly"].minimum_history == 60
    for profile in PROFILES.values():
        assert sum(
            (
                profile.economic,
                profile.historical,
                profile.trend,
                profile.acceleration,
                profile.persistence,
                profile.regime,
            )
        ) == Decimal("1")


def test_score_exposes_economic_historical_effective_and_agreement() -> None:
    score = score_indicator_v2(
        indicator_code="vix",
        frequency="daily",
        direction=RiskDirection.HIGHER_IS_WORSE,
        economic_score=Decimal(".55"),
        features=_features("vix"),
        history_count=300,
        freshness=DataFreshness.FRESH,
    )

    assert score.economic_band is IndicatorBand.DANGER
    assert score.historical_band is IndicatorBand.CRITICAL
    assert score.effective_score is not None
    assert score.effective_band in {IndicatorBand.DANGER, IndicatorBand.CRITICAL}
    assert score.agreement is IndicatorAgreement.CONFIRMED_STRESS
    assert len(score.input_checksum) == 64


def test_indicator_score_ablation_variants_are_explicit_and_deterministic() -> None:
    base = score_indicator_v2(
        indicator_code="vix",
        frequency="daily",
        direction=RiskDirection.HIGHER_IS_WORSE,
        economic_score=Decimal(".55"),
        features=_features("vix"),
        history_count=300,
        freshness=DataFreshness.FRESH,
    )

    variants = {name: score_variant(base, name) for name in SCORING_VARIANTS}

    assert variants["economic_only"].effective_score == Decimal(".5500")
    assert variants["historical_only"].effective_score == base.historical_score
    assert variants["without_trend"].effective_score != base.effective_score
    assert variants["without_events"].effective_score == base.effective_score
    assert variants["without_contagion"].effective_score == base.effective_score
    assert len({item.input_checksum for item in variants.values()}) == len(variants)


def test_insufficient_history_is_explicit_and_weights_are_renormalized() -> None:
    score = score_indicator_v2(
        indicator_code="monthly_macro",
        frequency="monthly",
        direction=RiskDirection.LOWER_IS_WORSE,
        economic_score=Decimal(".10"),
        features=_features("monthly_macro", percentile=".01", zscore="-3", change="-2"),
        history_count=12,
        freshness=DataFreshness.FRESH,
    )

    assert score.historical_score is None
    assert score.historical_band is None
    assert score.agreement is IndicatorAgreement.INSUFFICIENT_HISTORY
    assert score.effective_score is not None


def test_stale_data_never_becomes_a_numeric_v2_score() -> None:
    score = score_indicator_v2(
        indicator_code="vix",
        frequency="daily",
        direction=RiskDirection.HIGHER_IS_WORSE,
        economic_score=Decimal("1"),
        features=_features("vix"),
        history_count=300,
        freshness=DataFreshness.STALE,
    )

    assert score.effective_score is None
    assert score.effective_band is None
    assert score.agreement is IndicatorAgreement.INSUFFICIENT_DATA


def test_stage_uses_independent_clusters_and_fails_closed_on_coverage() -> None:
    specs = (
        ("credit_a", "credit", "US"),
        ("credit_b", "global_credit_cycle", "GLOBAL"),
        ("vix", "market_stress", "US"),
        ("oil", "inflation_commodities", "GLOBAL"),
    )
    scores = tuple(
        score_indicator_v2(
            indicator_code=code,
            frequency="daily",
            direction=RiskDirection.HIGHER_IS_WORSE,
            economic_score=Decimal(".9"),
            features=_features(code),
            history_count=300,
            freshness=DataFreshness.FRESH,
        )
        for code, _, _ in specs
    )
    assignments = {
        code: dependency_for(code=code, group_code=group, region_code=region)
        for code, group, region in specs
    }
    healthy = calculate_stage_v2(
        scores,
        assignments,
        coverage_status=CoverageStatus.HEALTHY,
    )
    unavailable = calculate_stage_v2(
        scores,
        assignments,
        coverage_status=CoverageStatus.INSUFFICIENT_DATA,
    )

    assert healthy.active_independent_clusters == 3
    assert healthy.stress_intensity > Decimal("75")
    assert unavailable.stage == "insufficient_data"
    assert unavailable.calculated_stage == healthy.calculated_stage


def test_v11_catalog_has_complete_immutable_metadata_and_dependency_graph(tmp_path) -> None:
    database = Database(tmp_path / "v11.sqlite3")
    repository = CrisisRadarRepository(database)
    first = bootstrap_v11_catalog(repository)
    second = bootstrap_v11_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V11_VERSION
    with database.connect() as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION
        thresholds = connection.execute(
            """
            SELECT count(*), min(length(metadata_checksum)), min(length(rationale_payload)),
                   min(length(source_url)), min(length(profile)),
                   min(length(promotion_evidence_payload))
            FROM cr_threshold_sets AS threshold_set
            JOIN cr_methodology_versions AS methodology
              ON methodology.id=threshold_set.methodology_id
            WHERE methodology.version=?
            """,
            (METHODOLOGY_V11_VERSION,),
        ).fetchone()
        assert thresholds[0] == len(V11_INDICATORS)
        assert thresholds[1] == 64
        assert all(value > 0 for value in thresholds[2:])
        assert connection.execute(
            """
            SELECT count(*) FROM cr_entity_metadata
            WHERE metadata_version='v11' AND entity_type='indicator'
            """
        ).fetchone()[0] == len(V11_INDICATORS)
        assert connection.execute(
            "SELECT count(*) FROM cr_dependency_assignments"
        ).fetchone()[0] == len(V11_INDICATORS)


def test_next_depth_series_are_disabled_research_and_do_not_mutate_v11(tmp_path) -> None:
    database = Database(tmp_path / "v11-depth-research.sqlite3")
    repository = CrisisRadarRepository(database)
    checksum_before = methodology_checksum(
        version=METHODOLOGY_V11_VERSION,
        indicators=V11_INDICATORS,
        scenarios=V11_SCENARIOS,
    )

    bootstrap_v11_catalog(repository)

    checksum_after = methodology_checksum(
        version=METHODOLOGY_V11_VERSION,
        indicators=V11_INDICATORS,
        scenarios=V11_SCENARIOS,
    )
    research_codes = {item.code for item in FRED_V12_RESEARCH_INDICATORS}
    assert research_codes.isdisjoint({item.code for item in V11_INDICATORS})
    assert checksum_after == checksum_before
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT code, enabled
            FROM cr_indicator_definitions
            WHERE code IN ({})
            """.format(",".join("?" for _ in research_codes)),
            tuple(sorted(research_codes)),
        ).fetchall()
        threshold_count = connection.execute(
            """
            SELECT count(*)
            FROM cr_threshold_sets AS threshold_set
            JOIN cr_indicator_definitions AS indicator
              ON indicator.id=threshold_set.indicator_id
            WHERE indicator.code IN ({})
            """.format(",".join("?" for _ in research_codes)),
            tuple(sorted(research_codes)),
        ).fetchone()[0]
    assert {row[0] for row in rows} == research_codes
    assert all(row[1] == 0 for row in rows)
    assert threshold_count == 0


def test_service_persists_v11_as_shadow_without_replacing_v10(tmp_path) -> None:
    database = Database(tmp_path / "shadow.sqlite3")
    repository = CrisisRadarRepository(database)
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            coverage_gate=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )
    service.bootstrap()
    for index in range(300):
        observed_at = NOW.replace(hour=0) - timedelta(days=299 - index)
        repository.save_observation(
            Observation(
                indicator_code="vix",
                source_code="fred",
                value=Decimal(str(15 + index / 50)),
                unit="index_points",
                observed_at=observed_at,
                released_at=observed_at,
                fetched_at=observed_at,
                vintage=observed_at.date().isoformat(),
            )
        )

    overview = service.recompute(snapshot_at=NOW)
    shadow = service.v2_shadow(locale="ru")

    assert overview is not None
    assert service.methodology_version != METHODOLOGY_V11_VERSION
    assert shadow["ready"] is True
    assert shadow["methodology"]["version"] == METHODOLOGY_V11_VERSION
    assert shadow["stage"] == "insufficient_data"
    assert shadow["items"][0]["name"] == "Индекс волатильности VIX"
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM cr_shadow_comparisons").fetchone()[0] == 1


def test_v11_replay_is_causal_and_future_release_cannot_change_past_signal(tmp_path) -> None:
    database = Database(tmp_path / "v11-replay.sqlite3")
    repository = CrisisRadarRepository(database)
    CrisisRadarService(repository).bootstrap()
    cutoff = NOW - timedelta(days=30)
    for index in range(300):
        observed_at = cutoff - timedelta(days=299 - index)
        repository.save_observation(
            Observation(
                indicator_code="vix",
                source_code="fred",
                value=Decimal(str(15 + index / 100)),
                unit="index_points",
                observed_at=observed_at,
                released_at=observed_at,
                fetched_at=observed_at,
                vintage=f"initial-{index}",
            )
        )

    before = v11_signals_as_of(
        repository,
        scenario_code="financial_stress",
        snapshot_at=cutoff,
        minimum_coverage=Decimal("0"),
    )
    repository.save_observation(
        Observation(
            indicator_code="vix",
            source_code="fred",
            value=Decimal("90"),
            unit="index_points",
            observed_at=cutoff,
            released_at=cutoff + timedelta(days=10),
            fetched_at=cutoff + timedelta(days=10),
            vintage="future-release",
        )
    )
    after = v11_signals_as_of(
        repository,
        scenario_code="financial_stress",
        snapshot_at=cutoff,
        minimum_coverage=Decimal("0"),
    )

    assert [item.canonical_payload() for item in before] == [
        item.canonical_payload() for item in after
    ]
    assert all(item.latest_released_at is None or item.latest_released_at <= cutoff for item in after)
    replay = replay_v11_scenario(
        repository,
        "financial_stress",
        started_at=cutoff,
        ended_at=cutoff + timedelta(days=1),
        step=timedelta(days=1),
        minimum_coverage=Decimal("0"),
    )
    assert replay.checksum == replay_v11_scenario(
        repository,
        "financial_stress",
        started_at=cutoff,
        ended_at=cutoff + timedelta(days=1),
        step=timedelta(days=1),
        minimum_coverage=Decimal("0"),
    ).checksum


def test_v11_comparison_manifest_never_promotes_or_invents_probability(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "v11-manifest.sqlite3"))
    CrisisRadarService(repository).bootstrap()

    manifest = execute_v11_comparison(
        repository,
        scenario_code="financial_stress",
        started_at=datetime(2008, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2008, 2, 1, tzinfo=timezone.utc),
        cadence_days=30,
        horizon_days=30,
        minimum_coverage=Decimal(".70"),
    )

    assert tuple(manifest["results"]) == (
        "v10_baseline",
        "economic_only",
        "historical_only",
        "full",
        "without_trend",
        "without_events",
        "without_contagion",
        "without_dependency_correction",
        "naive_base_rate",
    )
    assert manifest["candidate_status"] == "shadow"
    assert manifest["promotion_gate"]["passed"] is False
    assert manifest["live_probability"] is None
    assert len(manifest["manifest_checksum"]) == 64
