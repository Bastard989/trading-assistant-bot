from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from trading_bot.crisis_radar.postgres_memory import connect_postgres


def _canonical(row: sqlite3.Row) -> str:
    return json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(rows: list[tuple[str, str, str]]) -> str:
    digest = hashlib.sha256()
    for table, key, row_hash in sorted(rows):
        digest.update(f"{table}\0{key}\0{row_hash}\n".encode())
    return digest.hexdigest()


def migrate(sqlite_path: Path, dsn: str, schema_path: Path) -> dict:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    started_at = datetime.now(timezone.utc)
    source_rows: list[tuple[str, str, str]] = []
    source_counts: dict[str, int] = {}
    documents: list[dict] = []
    graph_edges: list[tuple] = []
    with sqlite3.connect(sqlite_path) as source:
        source.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        table_payloads = {}
        for table in tables:
            rows = source.execute(f'SELECT rowid AS __shadow_rowid__, * FROM "{table}"').fetchall()
            payloads = []
            for row in rows:
                payload = dict(row)
                key = str(payload.pop("id", payload.pop("__shadow_rowid__")))
                payload.pop("__shadow_rowid__", None)
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                row_hash = hashlib.sha256(encoded.encode()).hexdigest()
                payloads.append((key, encoded, row_hash))
                source_rows.append((table, key, row_hash))
            source_counts[table] = len(payloads)
            table_payloads[table] = payloads
        news_columns = {row[1] for row in source.execute("PRAGMA table_info(cr_news_items)")}
        if "cr_news_items" in tables:
            rows = source.execute(
                """
                SELECT item.*, source.code AS source_code, source.name AS source_name
                FROM cr_news_items AS item JOIN cr_sources AS source ON source.id=item.source_id
                ORDER BY item.id
                """
            ).fetchall()
            for row in rows:
                item = dict(row)
                documents.append(
                    {
                        "id": int(item["id"]),
                        "source_code": item["source_code"],
                        "source_tier": item.get("source_tier", "A"),
                        "publisher": item.get("publisher") or item["source_name"],
                        "published_at": item["published_at"],
                        "fetched_at": item["fetched_at"],
                        "title": item["title"],
                        "body": item["summary"],
                        "url": item["url"],
                        "original_language": item.get("original_language") or item["language"],
                        "content_hash": item["content_hash"],
                        "metadata": {
                            "category": item["category"],
                            "importance": item["importance"],
                            "available_columns": sorted(news_columns),
                        },
                    }
                )
        if "cr_news_evidence" in tables:
            rows = source.execute(
                """
                SELECT evidence.news_item_id, scenario.code AS scenario_code,
                       evidence.relevance_score_text, evidence.created_at
                FROM cr_news_evidence AS evidence
                JOIN cr_scenario_definitions AS scenario ON scenario.id=evidence.scenario_id
                ORDER BY evidence.id
                """
            ).fetchall()
            merged_edges: dict[tuple[int, str], dict] = {}
            for row in rows:
                key = (int(row["news_item_id"]), str(row["scenario_code"]))
                current = merged_edges.setdefault(
                    key,
                    {
                        "created_at": row["created_at"],
                        "confidence": row["relevance_score_text"],
                    },
                )
                current["created_at"] = min(current["created_at"], row["created_at"])
                current["confidence"] = str(
                    max(float(current["confidence"]), float(row["relevance_score_text"]))
                )
            graph_edges = [
                (
                    "document", str(news_item_id), "supports", "scenario", scenario_code,
                    news_item_id, values["created_at"], values["confidence"],
                )
                for (news_item_id, scenario_code), values in sorted(merged_edges.items())
            ]

    with connect_postgres(dsn) as target:
        with target.cursor() as cursor:
            cursor.execute(schema_path.read_text())
            cursor.execute("TRUNCATE crisis_radar_memory.shadow_rows")
            cursor.execute(
                "TRUNCATE crisis_radar_memory.edges, crisis_radar_memory.chunks, "
                "crisis_radar_memory.documents, crisis_radar_memory.events RESTART IDENTITY CASCADE"
            )
            for table, payloads in table_payloads.items():
                cursor.executemany(
                    """
                    INSERT INTO crisis_radar_memory.shadow_rows(table_name, row_key, row_payload, row_hash)
                    VALUES (%s, %s, %s::jsonb, %s)
                    """,
                    [(table, key, payload, row_hash) for key, payload, row_hash in payloads],
                )
            cursor.executemany(
                """
                INSERT INTO crisis_radar_memory.documents(
                    id, source_code, source_tier, publisher, published_at, fetched_at,
                    title, body, url, original_language, content_hash, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                [
                    (
                        item["id"], item["source_code"], item["source_tier"], item["publisher"],
                        item["published_at"], item["fetched_at"], item["title"], item["body"],
                        item["url"], item["original_language"], item["content_hash"],
                        json.dumps(item["metadata"], ensure_ascii=False, sort_keys=True),
                    )
                    for item in documents
                ],
            )
            cursor.executemany(
                """
                INSERT INTO crisis_radar_memory.chunks(
                    document_id, ordinal, text, content_hash, metadata
                ) VALUES (%s, 0, %s, %s, '{}'::jsonb)
                """,
                [
                    (
                        item["id"], f'{item["title"]}\n{item["body"]}'.strip(),
                        hashlib.sha256(f'{item["title"]}\n{item["body"]}'.strip().encode()).hexdigest(),
                    )
                    for item in documents
                ],
            )
            cursor.executemany(
                """
                INSERT INTO crisis_radar_memory.edges(
                    from_kind, from_id, relation, to_kind, to_id, evidence_document_id,
                    valid_from, confidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                graph_edges,
            )
            cursor.execute(
                "SELECT table_name, row_key, row_hash FROM crisis_radar_memory.shadow_rows ORDER BY 1,2"
            )
            target_rows = [(str(a), str(b), str(c)) for a, b, c in cursor.fetchall()]
            cursor.execute(
                "SELECT table_name, count(*) FROM crisis_radar_memory.shadow_rows GROUP BY table_name"
            )
            counted = {str(table): int(count) for table, count in cursor.fetchall()}
            target_counts = {table: counted.get(table, 0) for table in source_counts}
            source_checksum = _checksum(source_rows)
            target_checksum = _checksum(target_rows)
            parity = source_counts == target_counts and source_checksum == target_checksum
            cursor.execute(
                """
                INSERT INTO crisis_radar_memory.migration_manifests(
                    sqlite_path_hash, started_at, completed_at, source_counts, target_counts,
                    source_checksum, target_checksum, parity
                ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
                """,
                (
                    hashlib.sha256(str(sqlite_path.resolve()).encode()).hexdigest(), started_at,
                    datetime.now(timezone.utc), json.dumps(source_counts, sort_keys=True),
                    json.dumps(target_counts, sort_keys=True), source_checksum, target_checksum, parity,
                ),
            )
        target.commit()
    return {
        "parity": parity,
        "table_count": len(source_counts),
        "row_count": sum(source_counts.values()),
        "source_checksum": source_checksum,
        "target_checksum": target_checksum,
        "source_counts": source_counts,
        "target_counts": target_counts,
        "evidence_document_count": len(documents),
        "evidence_edge_count": len(graph_edges),
    }


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--dsn", default=os.getenv("CRISIS_POSTGRES_DSN", ""))
    parser.add_argument(
        "--schema", type=Path, default=Path("deploy/postgres/crisis_radar_memory.sql")
    )
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("CRISIS_POSTGRES_DSN or --dsn is required")
    result = migrate(args.sqlite, args.dsn, args.schema)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["parity"] else 1)


if __name__ == "__main__":
    main()
