from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


CANARY_VERSION = "crisis-radar-canary-v2"
CANARY_DURATION = timedelta(days=14)
CANARY_MINIMUM_SAMPLES = 1210  # 90% of a fifteen-minute schedule across 14 days.


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _parse(value: str | None) -> datetime | None:
    return None if not value else datetime.fromisoformat(value).astimezone(timezone.utc)


def _latest_verified_backup(directory: Path, *, now: datetime) -> dict:
    if not directory.exists():
        return {"status": "missing", "age_seconds": None, "checksum_valid": False}
    candidates = sorted(directory.glob("*.sqlite3"), key=lambda item: item.stat().st_mtime)
    if not candidates:
        return {"status": "missing", "age_seconds": None, "checksum_valid": False}
    backup = candidates[-1]
    sidecar = backup.with_suffix(backup.suffix + ".sha256")
    expected = ""
    if sidecar.exists():
        expected = sidecar.read_text(encoding="ascii").split(maxsplit=1)[0].strip()
    actual = hashlib.sha256(backup.read_bytes()).hexdigest()
    age = max(0, int((now - datetime.fromtimestamp(backup.stat().st_mtime, timezone.utc)).total_seconds()))
    return {
        "status": "healthy" if expected == actual else "invalid",
        "age_seconds": age,
        "checksum_valid": bool(expected) and expected == actual,
        "filename": backup.name,
    }


def collect_database_metrics(
    database_path: Path,
    *,
    backup_directory: Path,
    now: datetime,
) -> dict:
    _aware(now, "now")
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        snapshot = connection.execute(
            """
            SELECT snapshot_at, stage, calculated_stage, coverage_status,
                   stress_intensity_text, systemic_breadth_text
            FROM cr_market_snapshots_v2
            ORDER BY snapshot_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        news = connection.execute(
            """
            SELECT snapshot_at, status, ratio_text, failed_source_count
            FROM cr_news_coverage_snapshots
            ORDER BY snapshot_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        failed_sources = connection.execute(
            """
            SELECT source.code, source.access_type, source.status FROM (
                SELECT source.id, source.code, source.access_type, run.status
                FROM cr_sources AS source
                JOIN cr_sync_runs AS run ON run.id=(
                    SELECT latest.id FROM cr_sync_runs AS latest
                    WHERE latest.source_id=source.id ORDER BY latest.id DESC LIMIT 1
                )
                WHERE source.enabled=1 AND run.status IN ('failed', 'partial')
            ) AS source
            ORDER BY source.code
            """
        ).fetchall()
        alert_queue = connection.execute(
            """
            SELECT count(*) AS queued,
                   sum(CASE WHEN attempts > 0 THEN 1 ELSE 0 END) AS retried
            FROM cr_alert_deliveries WHERE status IN ('pending', 'failed')
            """
        ).fetchone()
        health_queue = connection.execute(
            """
            SELECT count(*) AS queued,
                   sum(CASE WHEN attempts > 0 THEN 1 ELSE 0 END) AS retried
            FROM cr_data_health_deliveries WHERE status IN ('pending', 'failed')
            """
        ).fetchone()
    snapshot_at = None if snapshot is None else _parse(snapshot["snapshot_at"])
    news_at = None if news is None else _parse(news["snapshot_at"])
    snapshot_lag = None if snapshot_at is None else max(0, int((now - snapshot_at).total_seconds()))
    news_lag = None if news_at is None else max(0, int((now - news_at).total_seconds()))
    return {
        "database_integrity": integrity,
        "snapshot": None
        if snapshot is None
        else {
            "at": snapshot["snapshot_at"],
            "lag_seconds": snapshot_lag,
            "stage": snapshot["stage"],
            "calculated_stage": snapshot["calculated_stage"],
            "coverage_status": snapshot["coverage_status"],
            "intensity": snapshot["stress_intensity_text"],
            "breadth": snapshot["systemic_breadth_text"],
        },
        "news_coverage": None
        if news is None
        else {
            "at": news["snapshot_at"],
            "lag_seconds": news_lag,
            "status": news["status"],
            "ratio": news["ratio_text"],
            "failed_sources": int(news["failed_source_count"]),
        },
        "source_failures": sum(
            row["access_type"] not in {"discovery_api", "research_candidate"}
            for row in failed_sources
        ),
        "source_failure_codes": [
            row["code"] for row in failed_sources
            if row["access_type"] not in {"discovery_api", "research_candidate"}
        ],
        "discovery_source_failures": sum(
            row["access_type"] == "discovery_api" for row in failed_sources
        ),
        "discovery_source_failure_codes": [
            row["code"] for row in failed_sources
            if row["access_type"] == "discovery_api"
        ],
        "research_source_failures": sum(
            row["access_type"] == "research_candidate" for row in failed_sources
        ),
        "research_source_failure_codes": [
            row["code"] for row in failed_sources
            if row["access_type"] == "research_candidate"
        ],
        "queues": {
            "alerts": int(alert_queue["queued"] or 0),
            "alert_retries": int(alert_queue["retried"] or 0),
            "data_health": int(health_queue["queued"] or 0),
            "data_health_retries": int(health_queue["retried"] or 0),
        },
        "backup": _latest_verified_backup(backup_directory, now=now),
        "disk_bytes": database_path.stat().st_size,
    }


def evaluate_sample(
    metrics: dict,
    *,
    http_health: dict,
    max_snapshot_lag_seconds: int = 7200,
    max_backup_age_seconds: int = 36 * 3600,
) -> tuple[dict, ...]:
    incidents = []

    def add(code: str, severity: str, detail: str) -> None:
        incidents.append({"code": code, "severity": severity, "detail": detail})

    if not http_health.get("live") or not http_health.get("ready"):
        add("http_health_failed", "critical", "live or ready endpoint failed")
    if metrics.get("database_integrity") != "ok":
        add("database_integrity_failed", "critical", str(metrics.get("database_integrity")))
    snapshot = metrics.get("snapshot")
    if snapshot is None:
        add("snapshot_missing", "critical", "candidate-v11 snapshot is absent")
    else:
        if snapshot.get("lag_seconds") is None or snapshot["lag_seconds"] > max_snapshot_lag_seconds:
            add("snapshot_stale", "critical", f"lag_seconds={snapshot.get('lag_seconds')}")
        if snapshot.get("stage") == "stable" and snapshot.get("coverage_status") == "insufficient_data":
            add("false_stable", "critical", "stable stage with insufficient numeric coverage")
    news = metrics.get("news_coverage")
    if news is None or news.get("status") == "insufficient_data":
        add("news_blackout", "critical", "news coverage is absent or insufficient")
    if metrics.get("source_failures", 0):
        add(
            "source_failures",
            "warning",
            json.dumps(
                {
                    "count": metrics["source_failures"],
                    "codes": metrics.get("source_failure_codes", []),
                },
                sort_keys=True,
            ),
        )
    if metrics.get("discovery_source_failures", 0):
        add(
            "discovery_source_failures",
            "warning",
            json.dumps(
                {
                    "count": metrics["discovery_source_failures"],
                    "codes": metrics.get("discovery_source_failure_codes", []),
                },
                sort_keys=True,
            ),
        )
    if metrics.get("research_source_failures", 0):
        add(
            "research_source_failures",
            "warning",
            json.dumps(
                {
                    "count": metrics["research_source_failures"],
                    "codes": metrics.get("research_source_failure_codes", []),
                },
                sort_keys=True,
            ),
        )
    queues = metrics.get("queues") or {}
    if queues.get("alerts", 0) > 100 or queues.get("data_health", 0) > 100:
        add("delivery_queue_growth", "critical", json.dumps(queues, sort_keys=True))
    backup = metrics.get("backup") or {}
    if not backup.get("checksum_valid"):
        add("backup_invalid", "critical", str(backup.get("status")))
    elif backup.get("age_seconds") is None or backup["age_seconds"] > max_backup_age_seconds:
        add("backup_stale", "critical", f"age_seconds={backup.get('age_seconds')}")
    return tuple(incidents)


def update_canary_manifest(
    manifest_path: Path,
    *,
    sample_at: datetime,
    release: str,
    methodology: str,
    metrics: dict,
    http_health: dict,
) -> dict:
    _aware(sample_at, "sample_at")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("release") != release or manifest.get("methodology") != methodology:
            raise RuntimeError("canary release/methodology changed; start a new manifest path")
    else:
        manifest = {
            "version": CANARY_VERSION,
            "release": release,
            "methodology": methodology,
            "started_at": sample_at.isoformat(),
            "expected_end_at": (sample_at + CANARY_DURATION).isoformat(),
            "status": "in_progress",
            "minimum_sample_count": CANARY_MINIMUM_SAMPLES,
            "sample_count": 0,
            "incident_count": 0,
            "critical_incident_count": 0,
            "resolution_count": 0,
            "active_incidents": [],
            "resolved_incidents": [],
            "restart_intervals": [],
            "incidents": [],
        }
    incidents = evaluate_sample(metrics, http_health=http_health)
    previous_at = _parse(manifest.get("last_sample_at"))
    if previous_at is not None and sample_at - previous_at > timedelta(hours=1):
        manifest["restart_intervals"].append(
            {"from": previous_at.isoformat(), "to": sample_at.isoformat()}
        )
    previous_active = {
        item["code"]: item
        for item in manifest.get("active_incidents", [])
        if isinstance(item, dict) and item.get("code")
    }
    current_by_code = {item["code"]: item for item in incidents}
    opened = []
    active = []
    for code, incident in sorted(current_by_code.items()):
        previous = previous_active.get(code)
        opened_at = (
            sample_at.isoformat()
            if previous is None
            else str(previous.get("opened_at") or sample_at.isoformat())
        )
        if previous is None:
            record = {"at": sample_at.isoformat(), **incident}
            manifest["incidents"].append(record)
            opened.append(incident)
        active.append(
            {
                **incident,
                "opened_at": opened_at,
                "last_seen_at": sample_at.isoformat(),
            }
        )
    resolved = []
    for code, previous in sorted(previous_active.items()):
        if code in current_by_code:
            continue
        record = {
            "code": code,
            "severity": previous.get("severity", "warning"),
            "detail": previous.get("detail", ""),
            "opened_at": previous.get("opened_at"),
            "resolved_at": sample_at.isoformat(),
        }
        manifest.setdefault("resolved_incidents", []).append(record)
        resolved.append(record)
    manifest["active_incidents"] = active
    manifest["sample_count"] += 1
    manifest["incident_count"] += len(opened)
    manifest["critical_incident_count"] += sum(
        item["severity"] == "critical" for item in opened
    )
    manifest["resolution_count"] = manifest.get("resolution_count", 0) + len(resolved)
    manifest["last_sample_at"] = sample_at.isoformat()
    manifest["last_metrics"] = metrics
    manifest["last_http_health"] = http_health
    expected_end = _parse(manifest["expected_end_at"])
    assert expected_end is not None
    if sample_at >= expected_end:
        density_recorded = any(
            item.get("code") == "insufficient_sample_density"
            for item in manifest["incidents"]
        )
        if manifest["sample_count"] < manifest["minimum_sample_count"] and not density_recorded:
            density_incident = {
                "at": sample_at.isoformat(),
                "code": "insufficient_sample_density",
                "severity": "critical",
                "detail": (
                    f"samples={manifest['sample_count']} required="
                    f"{manifest['minimum_sample_count']}"
                ),
            }
            manifest["incidents"].append(density_incident)
            manifest["incident_count"] += 1
            manifest["critical_incident_count"] += 1
        manifest["status"] = "passed" if manifest["critical_incident_count"] == 0 else "failed"
    canonical = json.dumps(
        {key: value for key, value in manifest.items() if key != "checksum"},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest["checksum"] = hashlib.sha256(canonical).hexdigest()
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest
