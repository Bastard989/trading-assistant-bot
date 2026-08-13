from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scripts.backup_sqlite import online_backup
from trading_bot.crisis_radar.canary import (
    collect_database_metrics,
    evaluate_sample,
    update_canary_manifest,
)
from trading_bot.crisis_radar.domain import Observation
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)


def _healthy_metrics() -> dict:
    return {
        "database_integrity": "ok",
        "snapshot": {
            "at": NOW.isoformat(),
            "lag_seconds": 60,
            "stage": "warning",
            "calculated_stage": "warning",
            "coverage_status": "healthy",
            "intensity": "45",
            "breadth": "40",
        },
        "news_coverage": {
            "at": NOW.isoformat(),
            "lag_seconds": 60,
            "status": "healthy",
            "ratio": "1",
            "failed_sources": 0,
        },
        "source_failures": 0,
        "source_failure_codes": [],
        "discovery_source_failures": 0,
        "discovery_source_failure_codes": [],
        "research_source_failures": 0,
        "research_source_failure_codes": [],
        "queues": {"alerts": 0, "alert_retries": 0, "data_health": 0, "data_health_retries": 0},
        "backup": {"status": "healthy", "age_seconds": 60, "checksum_valid": True},
        "disk_bytes": 100,
    }


def test_canary_collects_real_database_snapshot_and_verifies_backup(tmp_path) -> None:
    database_path = tmp_path / "radar.sqlite3"
    repository = CrisisRadarRepository(Database(database_path))
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            coverage_gate=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )
    service.bootstrap()
    repository.save_observation(
        Observation(
            indicator_code="vix",
            source_code="fred",
            value=Decimal("25"),
            unit="index_points",
            observed_at=NOW,
            released_at=NOW,
            fetched_at=NOW,
        )
    )
    service.recompute(snapshot_at=NOW)
    backup_directory = tmp_path / "backups"
    online_backup(database_path, backup_directory / "verified.sqlite3")

    metrics = collect_database_metrics(
        database_path,
        backup_directory=backup_directory,
        now=NOW + timedelta(minutes=1),
    )

    assert metrics["database_integrity"] == "ok"
    assert metrics["snapshot"]["lag_seconds"] == 60
    assert metrics["backup"]["checksum_valid"] is True
    assert metrics["source_failures"] == 0
    assert metrics["discovery_source_failures"] == 0
    assert metrics["research_source_failures"] == 0


def test_canary_separates_required_discovery_and_research_source_failures(tmp_path) -> None:
    database_path = tmp_path / "radar-source-health.sqlite3"
    repository = CrisisRadarRepository(Database(database_path))
    service = CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )
    service.bootstrap()
    for code in ("fred", "gdelt_discovery", "new_york_fed"):
        run_id = repository.start_sync_run(code, started_at=NOW)
        repository.finish_sync_run(
            run_id,
            finished_at=NOW,
            status="failed",
            rows_fetched=0,
            rows_written=0,
            error_code="source_error",
            error_detail="bounded test failure",
        )
    backup_directory = tmp_path / "backups"
    online_backup(database_path, backup_directory / "verified.sqlite3")

    metrics = collect_database_metrics(
        database_path,
        backup_directory=backup_directory,
        now=NOW + timedelta(minutes=1),
    )
    incidents = evaluate_sample(
        {
            **metrics,
            "snapshot": _healthy_metrics()["snapshot"],
            "news_coverage": _healthy_metrics()["news_coverage"],
        },
        http_health={"live": True, "ready": True},
    )

    assert metrics["source_failures"] == 1
    assert metrics["source_failure_codes"] == ["fred"]
    assert metrics["discovery_source_failures"] == 1
    assert metrics["discovery_source_failure_codes"] == ["gdelt_discovery"]
    assert metrics["research_source_failures"] == 1
    assert metrics["research_source_failure_codes"] == ["new_york_fed"]
    assert {item["code"] for item in incidents} == {
        "source_failures",
        "discovery_source_failures",
        "research_source_failures",
    }


def test_canary_detects_false_stable_and_persists_fourteen_day_manifest(tmp_path) -> None:
    unhealthy = _healthy_metrics()
    unhealthy["snapshot"] = {
        **unhealthy["snapshot"],
        "stage": "stable",
        "coverage_status": "insufficient_data",
    }
    incidents = evaluate_sample(unhealthy, http_health={"live": True, "ready": True})
    assert any(item["code"] == "false_stable" for item in incidents)

    path = tmp_path / "canary.json"
    first = update_canary_manifest(
        path,
        sample_at=NOW,
        release="release-1",
        methodology="candidate-v11",
        metrics=_healthy_metrics(),
        http_health={"live": True, "ready": True},
    )
    assert first["status"] == "in_progress"
    assert first["expected_end_at"] == (NOW + timedelta(days=14)).isoformat()
    duration = timedelta(days=14)
    for index in range(1, first["minimum_sample_count"]):
        update_canary_manifest(
            path,
            sample_at=NOW + duration * (index / first["minimum_sample_count"]),
            release="release-1",
            methodology="candidate-v11",
            metrics=_healthy_metrics(),
            http_health={"live": True, "ready": True},
        )
    completed = update_canary_manifest(
        path,
        sample_at=NOW + timedelta(days=14),
        release="release-1",
        methodology="candidate-v11",
        metrics=_healthy_metrics(),
        http_health={"live": True, "ready": True},
    )
    assert completed["status"] == "passed"
    assert completed["sample_count"] >= completed["minimum_sample_count"]
    assert completed["active_incidents"] == []
    assert len(completed["checksum"]) == 64
    with pytest.raises(RuntimeError, match="release/methodology changed"):
        update_canary_manifest(
            path,
            sample_at=NOW + timedelta(days=15),
            release="release-2",
            methodology="candidate-v11",
            metrics=_healthy_metrics(),
            http_health={"live": True, "ready": True},
        )


def test_canary_deduplicates_active_incidents_and_records_resolution(tmp_path) -> None:
    path = tmp_path / "canary.json"
    warning = _healthy_metrics()
    warning["source_failures"] = 1

    first = update_canary_manifest(
        path,
        sample_at=NOW,
        release="release-1",
        methodology="candidate-v11",
        metrics=warning,
        http_health={"live": True, "ready": True},
    )
    second = update_canary_manifest(
        path,
        sample_at=NOW + timedelta(minutes=15),
        release="release-1",
        methodology="candidate-v11",
        metrics=warning,
        http_health={"live": True, "ready": True},
    )

    assert first["incident_count"] == 1
    assert second["incident_count"] == 1
    assert len(second["incidents"]) == 1
    assert second["active_incidents"][0]["code"] == "source_failures"
    assert second["active_incidents"][0]["opened_at"] == NOW.isoformat()

    resolved = update_canary_manifest(
        path,
        sample_at=NOW + timedelta(minutes=30),
        release="release-1",
        methodology="candidate-v11",
        metrics=_healthy_metrics(),
        http_health={"live": True, "ready": True},
    )
    assert resolved["active_incidents"] == []
    assert resolved["resolution_count"] == 1
    assert resolved["resolved_incidents"][0]["resolved_at"] == (
        NOW + timedelta(minutes=30)
    ).isoformat()

    reopened = update_canary_manifest(
        path,
        sample_at=NOW + timedelta(minutes=45),
        release="release-1",
        methodology="candidate-v11",
        metrics=warning,
        http_health={"live": True, "ready": True},
    )
    assert reopened["incident_count"] == 2
    assert len(reopened["incidents"]) == 2
