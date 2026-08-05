from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.crisis_radar.catalog import FRED_INDICATORS, METHODOLOGY_GLOBAL_V2_VERSION
from trading_bot.crisis_radar.domain import Observation
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


def test_v2_master_flag_enables_all_components(monkeypatch) -> None:
    monkeypatch.setenv("CRISIS_RADAR_V2_ENABLED", "true")

    flags = CrisisRadarFeatureFlags.from_environment()

    assert all(flags.as_dict().values())


def test_component_flag_can_fail_closed_over_master(monkeypatch) -> None:
    monkeypatch.setenv("CRISIS_RADAR_V2_ENABLED", "true")
    monkeypatch.setenv("CRISIS_RADAR_NEWS_EVENTS_V2_ENABLED", "false")

    flags = CrisisRadarFeatureFlags.from_environment()

    assert flags.coverage_gate is True
    assert flags.news_events_v2 is False


def test_v2_defaults_to_disabled(monkeypatch) -> None:
    for name in (
        "CRISIS_RADAR_V2_ENABLED",
        "CRISIS_RADAR_COVERAGE_GATE_ENABLED",
        "CRISIS_RADAR_THRESHOLDS_V2_ENABLED",
        "CRISIS_RADAR_GLOBAL_SOURCES_V2_ENABLED",
        "CRISIS_RADAR_NEWS_EVENTS_V2_ENABLED",
        "CRISIS_RADAR_EVIDENCE_MEMORY_V2_ENABLED",
        "CRISIS_RADAR_TREND_ENGINE_V2_ENABLED",
        "CRISIS_RADAR_SCENARIO_FUSION_V2_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    assert not any(CrisisRadarFeatureFlags.from_environment().as_dict().values())


def test_overview_never_falls_back_to_snapshot_from_an_old_methodology(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "version-isolation.sqlite3"))
    starter = CrisisRadarService(repository, feature_flags=CrisisRadarFeatureFlags())
    starter.bootstrap()
    now = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    seed = FRED_INDICATORS[0]
    repository.save_observation(
        Observation(
            indicator_code=seed.code,
            source_code="fred",
            value=Decimal("0.1"),
            unit=seed.unit,
            observed_at=now,
            released_at=now,
            fetched_at=now,
        )
    )
    assert starter.recompute(snapshot_at=now) is not None
    assert starter.overview(locale="en")["ready"] is True

    v2 = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            coverage_gate=True,
            thresholds_v2=True,
            global_sources_v2=True,
        ),
    )
    v2.bootstrap()
    payload = v2.overview(locale="en")

    assert payload["ready"] is False
    assert payload["stage"] == "unknown"
    assert payload["methodology"] == {
        "code": "crisis-radar",
        "version": METHODOLOGY_GLOBAL_V2_VERSION,
    }
