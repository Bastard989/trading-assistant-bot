from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.backup_operations import verify_sqlite_backup
from scripts.backup_sqlite import online_backup


DERIVED_SNAPSHOT_TABLES = (
    "cr_indicator_states",
    "cr_group_states",
    "cr_market_snapshots",
    "cr_scenario_states",
    "cr_indicator_features",
    "cr_contagion_features",
    "cr_scenario_fusion_states",
    "cr_indicator_scores_v2",
    "cr_group_states_v2",
    "cr_market_snapshots_v2",
    "cr_shadow_comparisons",
    "cr_news_coverage_snapshots",
    "cr_scenario_states_v2",
)
PROTECTED_EVENT_TABLES = ("cr_alert_events", "cr_data_health_events")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("snapshot timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _existing_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _timestamp_rows(connection: sqlite3.Connection, tables: Iterable[str]) -> set[str]:
    result: set[str] = set()
    existing = _existing_tables(connection)
    for table in tables:
        if table not in existing:
            continue
        result.update(
            str(row[0])
            for row in connection.execute(
                f'SELECT DISTINCT snapshot_at FROM "{table}" WHERE snapshot_at IS NOT NULL'
            ).fetchall()
        )
    return result


def retention_plan(
    database: Path,
    *,
    as_of: datetime,
    recent_days: int = 2,
    hourly_days: int = 180,
) -> dict:
    """Plan loss-bounded downsampling of derived snapshots.

    Raw observations, news, events, alerts, scorecards and trades are never in
    scope. Every recent snapshot is retained; older history keeps one point per
    UTC hour and, after ``hourly_days``, one point per UTC day. Exact timestamps
    referenced by alert/data-health events are always protected.
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if recent_days < 1 or hourly_days <= recent_days:
        raise ValueError("retention requires 1 <= recent_days < hourly_days")
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
        all_text = _timestamp_rows(connection, DERIVED_SNAPSHOT_TABLES)
        protected = _timestamp_rows(connection, PROTECTED_EVENT_TABLES)
        existing = _existing_tables(connection)
        before_rows = {
            table: int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            for table in DERIVED_SNAPSHOT_TABLES
            if table in existing
        }
    parsed = sorted((_parse_utc(value), value) for value in all_text)
    recent_cutoff = as_of.astimezone(timezone.utc) - timedelta(days=recent_days)
    hourly_cutoff = as_of.astimezone(timezone.utc) - timedelta(days=hourly_days)
    keep = set(protected)
    hourly_buckets: set[tuple[int, int, int, int]] = set()
    daily_buckets: set[tuple[int, int, int]] = set()
    for moment, text in reversed(parsed):
        if moment >= recent_cutoff:
            keep.add(text)
        elif moment >= hourly_cutoff:
            bucket = (moment.year, moment.month, moment.day, moment.hour)
            if bucket not in hourly_buckets:
                keep.add(text)
                hourly_buckets.add(bucket)
        else:
            bucket = (moment.year, moment.month, moment.day)
            if bucket not in daily_buckets:
                keep.add(text)
                daily_buckets.add(bucket)
    if parsed:
        keep.update({parsed[0][1], parsed[-1][1]})
    remove = sorted(all_text - keep)
    return {
        "ok": True,
        "database": str(database),
        "policy": {"recent_days": recent_days, "hourly_days": hourly_days},
        "timestamps_before": len(all_text),
        "timestamps_kept": len(all_text) - len(remove),
        "timestamps_to_remove": len(remove),
        "protected_event_timestamps": len(protected & all_text),
        "rows_before": before_rows,
        "remove": remove,
    }


def apply_retention(database: Path, plan: dict) -> dict:
    remove = tuple(str(item) for item in plan.get("remove", ()))
    deleted = {table: 0 for table in DERIVED_SNAPSHOT_TABLES}
    if not remove:
        return {**plan, "applied": True, "rows_deleted": deleted}
    with sqlite3.connect(database, timeout=60) as connection:
        existing = _existing_tables(connection)
        for offset in range(0, len(remove), 400):
            chunk = remove[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            for table in DERIVED_SNAPSHOT_TABLES:
                if table not in existing:
                    continue
                cursor = connection.execute(
                    f'DELETE FROM "{table}" WHERE snapshot_at IN ({placeholders})',
                    chunk,
                )
                deleted[table] += max(0, int(cursor.rowcount))
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError("snapshot retention integrity verification failed")
    return {
        **{key: value for key, value in plan.items() if key != "remove"},
        "applied": True,
        "rows_deleted": deleted,
        "integrity": "ok",
        "foreign_key_errors": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply bounded Crisis Radar derived-snapshot retention"
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--recent-days", type=int, default=2)
    parser.add_argument("--hourly-days", type=int, default=180)
    parser.add_argument("--as-of", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--vacuum", action="store_true")
    args = parser.parse_args()
    as_of = (
        _parse_utc(args.as_of)
        if args.as_of
        else datetime.now(timezone.utc)
    )
    plan = retention_plan(
        args.database,
        as_of=as_of,
        recent_days=args.recent_days,
        hourly_days=args.hourly_days,
    )
    plan["remove_sample"] = plan["remove"][:10]
    if not args.apply:
        plan.pop("remove", None)
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return
    if args.backup_directory is None:
        parser.error("--apply requires --backup-directory")
    timestamp = as_of.strftime("%Y%m%dT%H%M%SZ")
    backup = args.backup_directory / f"pre-retention-{timestamp}.sqlite3"
    digest = online_backup(args.database, backup)
    verified = verify_sqlite_backup(backup)
    if not verified["ok"] or verified["sha256"] != digest:
        raise RuntimeError("pre-retention backup verification failed")
    result = apply_retention(args.database, plan)
    result["backup"] = {"path": str(backup), "sha256": digest, "verified": True}
    if args.vacuum:
        with sqlite3.connect(args.database, timeout=60) as connection:
            connection.execute("VACUUM")
        result["vacuum"] = True
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
