from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_bot.crisis_radar.catalog import (
    METHODOLOGY_GLOBAL_V2_VERSION,
    METHODOLOGY_V2_VERSION,
    V2_INDICATORS,
    bootstrap_starter_catalog,
    bootstrap_v2_catalog,
)
from trading_bot.crisis_radar.domain import Observation
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def test_v2_thresholds_are_separate_immutable_candidates(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "threshold-v2.sqlite3"))
    bootstrap_starter_catalog(repository)
    first = bootstrap_v2_catalog(repository)
    second = bootstrap_v2_catalog(repository)

    assert first == second
    assert first["methodology_version"] == METHODOLOGY_V2_VERSION
    with repository.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT indicator.code, thresholds.warning_value, thresholds.danger_value,
                   thresholds.critical_value, thresholds.basis,
                   thresholds.promotion_status, thresholds.rationale_payload
            FROM cr_threshold_sets AS thresholds
            JOIN cr_indicator_definitions AS indicator ON indicator.id = thresholds.indicator_id
            JOIN cr_methodology_versions AS methodology
              ON methodology.id = thresholds.methodology_id
            WHERE methodology.version = ?
            ORDER BY indicator.code
            """,
            (METHODOLOGY_V2_VERSION,),
        ).fetchall()
    by_code = {row["code"]: row for row in rows}

    assert len(rows) == 23
    assert tuple(by_code["sp500_30d_drawdown"][key] for key in (
        "warning_value", "danger_value", "critical_value"
    )) == ("-10", "-20", "-30")
    assert tuple(by_code["china_real_gdp_yoy"][key] for key in (
        "warning_value", "danger_value", "critical_value"
    )) == ("4", "3", "1")
    assert by_code["sahm_rule"]["basis"] == "hybrid"
    assert by_code["sahm_rule"]["promotion_status"] == "candidate"
    assert "official" in by_code["sahm_rule"]["rationale_payload"]


def test_v2_service_uses_candidate_methodology_without_changing_v1(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "service-v2.sqlite3"))
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(thresholds_v2=True),
    )
    bootstrapped = service.bootstrap()
    repository.save_observation(
        Observation(
            indicator_code="china_real_gdp_yoy",
            source_code="world_bank",
            value=Decimal("2.5"),
            unit="percent",
            observed_at=NOW,
            released_at=NOW,
            fetched_at=NOW,
        )
    )
    service.recompute(snapshot_at=NOW)
    payload = service.overview(locale="en")

    assert bootstrapped["methodology_version"] == METHODOLOGY_V2_VERSION
    assert payload["methodology"]["version"] == METHODOLOGY_V2_VERSION
    china = next(item for item in payload["indicators"] if item["code"] == "china_real_gdp_yoy")
    assert china["band"] == "danger"
    history = service.indicator_history("china_real_gdp_yoy")
    assert history is not None
    assert history["threshold_methodology"]["basis"] == "hybrid"
    assert history["threshold_methodology"]["promotion_status"] == "candidate"
    assert history["threshold_methodology"]["rationale"]["operational_role"] == "candidate_signal"


def test_threshold_metadata_is_immutable_inside_methodology(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "immutable-v2.sqlite3"))
    result = bootstrap_v2_catalog(repository)
    with repository.db.connect() as connection:
        indicator_id = connection.execute(
            "SELECT id FROM cr_indicator_definitions WHERE code='sahm_rule'"
        ).fetchone()[0]

    from trading_bot.crisis_radar.domain import IndicatorThresholds, RiskDirection

    with pytest.raises(RuntimeError, match="immutable methodology"):
        repository.register_thresholds(
            indicator_id,
            int(result["methodology_id"]),
            IndicatorThresholds(
                warning=Decimal("0.25"),
                danger=Decimal("0.50"),
                critical=Decimal("1.00"),
                direction=RiskDirection.HIGHER_IS_WORSE,
            ),
            basis="hybrid",
            promotion_status="candidate",
            rationale={"ru": "подменено", "en": "tampered"},
        )


def test_global_sources_use_a_new_immutable_methodology(tmp_path) -> None:
    service = CrisisRadarService(
        CrisisRadarRepository(Database(tmp_path / "global-v2.sqlite3")),
        feature_flags=CrisisRadarFeatureFlags(global_sources_v2=True),
    )

    bootstrapped = service.bootstrap()

    assert bootstrapped["methodology_version"] == METHODOLOGY_GLOBAL_V2_VERSION
    assert bootstrapped["indicator_count"] > len(V2_INDICATORS)
