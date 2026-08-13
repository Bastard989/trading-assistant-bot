from trading_bot.crisis_radar.catalog import (
    BIS_V14_CANDIDATE_INDICATORS,
    METHODOLOGY_V13_VERSION,
    METHODOLOGY_V14_VERSION,
    V13_INDICATORS,
    V13_SCENARIOS,
    V14_INDICATORS,
    V14_SCENARIOS,
    bootstrap_v13_catalog,
    bootstrap_v14_catalog,
    methodology_checksum,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


def test_v14_is_immutable_collection_only_and_preserves_v13(tmp_path) -> None:
    database = Database(tmp_path / "candidate-v14.sqlite3")
    repository = CrisisRadarRepository(database)
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )
    service.bootstrap()
    v13_before = bootstrap_v13_catalog(repository)
    v13_checksum = methodology_checksum(
        version=METHODOLOGY_V13_VERSION,
        indicators=V13_INDICATORS,
        scenarios=V13_SCENARIOS,
    )

    first = bootstrap_v14_catalog(repository)
    second = bootstrap_v14_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V14_VERSION
    assert first["indicator_count"] == len(V14_INDICATORS)
    assert methodology_checksum(
        version=METHODOLOGY_V13_VERSION,
        indicators=V13_INDICATORS,
        scenarios=V13_SCENARIOS,
    ) == v13_checksum
    assert bootstrap_v13_catalog(repository) == v13_before
    assert methodology_checksum(
        version=METHODOLOGY_V14_VERSION,
        indicators=V14_INDICATORS,
        scenarios=V14_SCENARIOS,
    ) != v13_checksum
    with database.connect() as connection:
        rows = connection.execute(
            """
                SELECT indicator.code, indicator.enabled, threshold.basis,
                       threshold.promotion_status, threshold.rationale_payload,
                       threshold.source_url, metadata.name_ru,
                       metadata.description_ru
            FROM cr_indicator_definitions AS indicator
            JOIN cr_threshold_sets AS threshold ON threshold.indicator_id=indicator.id
            JOIN cr_methodology_versions AS methodology
              ON methodology.id=threshold.methodology_id
            JOIN cr_entity_metadata AS metadata
              ON metadata.entity_type='indicator'
             AND metadata.entity_code=indicator.code
             AND metadata.metadata_version='v14'
            WHERE methodology.version=?
              AND indicator.code IN ({})
            ORDER BY indicator.code
            """.format(
                ",".join("?" for _ in BIS_V14_CANDIDATE_INDICATORS)
            ),
            (
                METHODOLOGY_V14_VERSION,
                *(item.code for item in BIS_V14_CANDIDATE_INDICATORS),
            ),
        ).fetchall()
        snapshots = connection.execute(
            """
            SELECT count(*)
            FROM cr_market_snapshots_v2 AS snapshot
            JOIN cr_methodology_versions AS methodology
              ON methodology.id=snapshot.methodology_id
            WHERE methodology.version=?
            """,
            (METHODOLOGY_V14_VERSION,),
        ).fetchone()[0]
    assert len(rows) == len(BIS_V14_CANDIDATE_INDICATORS)
    assert all(row["enabled"] == 0 for row in rows)
    assert all(row["basis"] == "hybrid" for row in rows)
    assert all(row["promotion_status"] == "candidate" for row in rows)
    assert all(row["rationale_payload"] not in {"", "{}"} for row in rows)
    assert all(row["source_url"].startswith("https://www.bis.org/") for row in rows)
    assert all(row["name_ru"] and row["description_ru"] for row in rows)
    assert snapshots == 0


def test_v14_adds_two_independent_regional_channels_without_double_counting() -> None:
    codes = {item.code for item in BIS_V14_CANDIDATE_INDICATORS}
    assert len(codes) == 20
    assert sum(code.endswith("_debt_service_gap") for code in codes) == 10
    assert sum(code.endswith("_real_house_price_yoy") for code in codes) == 10
    assert all(item.name_ru and item.name for item in BIS_V14_CANDIDATE_INDICATORS)
    financial = next(item for item in V14_SCENARIOS if item.code == "financial_stress")
    assert sum(code.endswith("_debt_service") for code in financial.group_codes) == 10
    assert sum(code.endswith("_housing_cycle") for code in financial.group_codes) == 10
