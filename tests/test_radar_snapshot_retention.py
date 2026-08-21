from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from scripts.radar_snapshot_retention import apply_retention, retention_plan


NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


def _database(path):
    with sqlite3.connect(path) as connection:
        for table in (
            "cr_market_snapshots",
            "cr_indicator_states",
            "cr_indicator_scores_v2",
        ):
            connection.execute(
                f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, snapshot_at TEXT NOT NULL)"
            )
        connection.execute(
            "CREATE TABLE cr_alert_events(id INTEGER PRIMARY KEY, snapshot_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE cr_data_health_events(id INTEGER PRIMARY KEY, snapshot_at TEXT NOT NULL)"
        )
        for hours in range(24 * 220):
            moment = NOW - timedelta(hours=hours)
            for minute in (0, 15, 30, 45):
                text = moment.replace(minute=minute).isoformat()
                for table in (
                    "cr_market_snapshots",
                    "cr_indicator_states",
                    "cr_indicator_scores_v2",
                ):
                    connection.execute(f"INSERT INTO {table}(snapshot_at) VALUES (?)", (text,))
        protected = (NOW - timedelta(days=200, minutes=-15)).isoformat()
        connection.execute("INSERT INTO cr_alert_events(snapshot_at) VALUES (?)", (protected,))
    return protected


def test_snapshot_retention_is_dry_run_and_preserves_events_and_raw_tables(tmp_path) -> None:
    database = tmp_path / "radar.sqlite3"
    protected = _database(database)

    plan = retention_plan(database, as_of=NOW, recent_days=14, hourly_days=180)

    assert plan["timestamps_to_remove"] > 10_000
    assert protected not in plan["remove"]
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT count(*) FROM cr_market_snapshots").fetchone()[0]
    result = apply_retention(database, plan)
    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT count(*) FROM cr_market_snapshots").fetchone()[0]
        assert connection.execute(
            "SELECT count(*) FROM cr_alert_events WHERE snapshot_at=?", (protected,)
        ).fetchone()[0] == 1
    assert before - after == plan["timestamps_to_remove"]
    assert result["integrity"] == "ok"
    assert result["foreign_key_errors"] == 0


def test_snapshot_retention_rejects_invalid_policy(tmp_path) -> None:
    database = tmp_path / "empty.sqlite3"
    sqlite3.connect(database).close()
    try:
        retention_plan(database, as_of=NOW, recent_days=14, hourly_days=14)
    except ValueError as exc:
        assert "recent_days < hourly_days" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("unsafe retention policy must fail")
