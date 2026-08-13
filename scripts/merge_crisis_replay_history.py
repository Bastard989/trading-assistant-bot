from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_bot.crisis_radar.catalog import bootstrap_v12_catalog  # noqa: E402
from trading_bot.crisis_radar.domain import QualityFlag  # noqa: E402
from trading_bot.crisis_radar.repositories import (  # noqa: E402
    CrisisRadarRepository,
    _rebuild_observation_revision_chain,
)
from trading_bot.db import CURRENT_SCHEMA_VERSION, Database  # noqa: E402


def merge_history(
    source_path: Path,
    destination_path: Path,
    *,
    source_codes: set[str] | None = None,
) -> dict[str, object]:
    """Copy non-retrospective observations between disposable replay databases.

    The merge rejects explicitly revised research rows. The replay query still
    enforces its own as-of, release-time and estimated-release staleness gates;
    copying a row never makes it causally eligible by itself.
    """

    source_path = source_path.resolve()
    destination_path = destination_path.resolve()
    if source_path == destination_path:
        raise ValueError("source and destination databases must differ")
    if not source_path.is_file() or not destination_path.is_file():
        raise ValueError("source and destination databases must already exist")
    source = Database(source_path, auto_migrate=False)
    destination = Database(destination_path, auto_migrate=False)
    for label, database in (("source", source), ("destination", destination)):
        with database.connect() as connection:
            version = connection.execute(
                "SELECT max(version) FROM schema_migrations"
            ).fetchone()[0]
            if version != CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"{label} schema is {version}, expected {CURRENT_SCHEMA_VERSION}"
                )
    repository = CrisisRadarRepository(destination)
    bootstrap_v12_catalog(repository)
    query = """
        SELECT indicator.code AS indicator_code, source.code AS source_code,
               observation.observed_at, observation.released_at,
               observation.fetched_at, observation.value_text, observation.unit,
               observation.vintage, observation.quality_flags,
               observation.content_hash
        FROM cr_observations AS observation
        JOIN cr_indicator_definitions AS indicator
          ON indicator.id=observation.indicator_id
        JOIN cr_sources AS source ON source.id=observation.source_id
    """
    parameters: tuple[str, ...] = ()
    if source_codes is not None:
        if not source_codes:
            raise ValueError("source_codes must not be empty")
        query += " WHERE source.code IN ({})".format(
            ",".join("?" for _ in source_codes)
        )
        parameters = tuple(sorted(source_codes))
    query += " ORDER BY observation.observed_at, observation.id"
    rows_read = inserted = duplicate = excluded_revised = unknown = revisions_rebuilt = 0
    equal_value_vintages_preserved = 0
    affected_keys: dict[tuple[int, int, str], str] = {}
    codes: set[str] = set()
    with source.connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    with destination.connect() as connection:
        registrations = {
            (row["indicator_code"], row["source_code"]): (
                int(row["indicator_id"]),
                int(row["source_id"]),
                row["unit"],
            )
            for row in connection.execute(
                """
                SELECT indicator.id AS indicator_id,
                       indicator.code AS indicator_code,
                       indicator.unit, source.id AS source_id,
                       source.code AS source_code
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id=indicator.source_id
                """
            )
        }
        existing = connection.execute(
            """
            SELECT id, indicator_id, source_id, observed_at, vintage, value_text
            FROM cr_observations
            ORDER BY indicator_id, source_id, observed_at,
                     released_at, fetched_at, id
            """
        ).fetchall()
        revision_count_before = connection.execute(
            "SELECT count(*) FROM cr_observation_revisions"
        ).fetchone()[0]
        reverse_revision_links_before = connection.execute(
            """
            SELECT count(*)
            FROM cr_observation_revisions AS revision
            JOIN cr_observations AS previous
              ON previous.id=revision.previous_observation_id
            JOIN cr_observations AS revised
              ON revised.id=revision.revised_observation_id
            WHERE previous.released_at > revised.released_at
            """
        ).fetchone()[0]
        unique_keys = {
            (int(row["indicator_id"]), int(row["source_id"]), row["observed_at"], row["vintage"])
            for row in existing
        }
        latest_by_observation = {
            (int(row["indicator_id"]), int(row["source_id"]), row["observed_at"]): (
                int(row["id"]),
                row["value_text"],
            )
            for row in existing
        }
        for row in rows:
            rows_read += 1
            flags = frozenset(
                QualityFlag(item) for item in json.loads(row["quality_flags"] or "[]")
            )
            if QualityFlag.RETROSPECTIVE_REVISED in flags:
                excluded_revised += 1
                continue
            registration = registrations.get(
                (row["indicator_code"], row["source_code"])
            )
            if registration is None:
                unknown += 1
                continue
            indicator_id, source_id, unit = registration
            if unit != row["unit"]:
                raise ValueError(
                    f"unit mismatch for {row['indicator_code']}: {row['unit']} != {unit}"
                )
            observation_key = (indicator_id, source_id, row["observed_at"])
            codes.add(row["indicator_code"])
            unique_key = (*observation_key, row["vintage"])
            previous = latest_by_observation.get(observation_key)
            affected_keys[observation_key] = max(
                affected_keys.get(observation_key, row["fetched_at"]),
                row["fetched_at"],
            )
            if unique_key in unique_keys:
                duplicate += 1
                continue
            cursor = connection.execute(
                """
                INSERT INTO cr_observations(
                    indicator_id, source_id, observed_at, released_at, fetched_at,
                    value_text, unit, vintage, quality_flags, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    indicator_id,
                    source_id,
                    row["observed_at"],
                    row["released_at"],
                    row["fetched_at"],
                    row["value_text"],
                    row["unit"],
                    row["vintage"],
                    row["quality_flags"],
                    row["content_hash"],
                ),
            )
            observation_id = int(cursor.lastrowid)
            if previous is not None and previous[1] == row["value_text"]:
                equal_value_vintages_preserved += 1
            inserted += 1
            unique_keys.add(unique_key)
            latest_by_observation[observation_key] = (
                observation_id,
                row["value_text"],
            )
        for (indicator_id, source_id, observed_at), detected_at in affected_keys.items():
            revisions_rebuilt += len(
                _rebuild_observation_revision_chain(
                    connection,
                    indicator_id=indicator_id,
                    source_id=source_id,
                    observed_at=observed_at,
                    detected_at=detected_at,
                )
            )
        revision_count_after = connection.execute(
            "SELECT count(*) FROM cr_observation_revisions"
        ).fetchone()[0]
        reverse_revision_links_after = connection.execute(
            """
            SELECT count(*)
            FROM cr_observation_revisions AS revision
            JOIN cr_observations AS previous
              ON previous.id=revision.previous_observation_id
            JOIN cr_observations AS revised
              ON revised.id=revision.revised_observation_id
            WHERE previous.released_at > revised.released_at
            """
        ).fetchone()[0]
    with destination.connect() as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    return {
        "source_database": str(source_path),
        "destination_database": str(destination_path),
        "rows_read": rows_read,
        "rows_inserted": inserted,
        "rows_duplicate": duplicate,
        "equal_value_vintages_preserved": equal_value_vintages_preserved,
        "rows_excluded_retrospective_revised": excluded_revised,
        "rows_unknown_indicator": unknown,
        "revision_links_created": max(0, revision_count_after - revision_count_before),
        "revision_links_rebuilt": revisions_rebuilt,
        "reverse_revision_links_before": reverse_revision_links_before,
        "reverse_revision_links_after": reverse_revision_links_after,
        "indicator_count": len(codes),
        "integrity": integrity,
        "foreign_key_violations": foreign_keys,
        "causal_eligibility_enforced_by_replay": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge causal observations into an existing disposable replay database"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-code", action="append", dest="source_codes")
    args = parser.parse_args()
    report = merge_history(
        args.source,
        args.destination,
        source_codes=set(args.source_codes) if args.source_codes else None,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
