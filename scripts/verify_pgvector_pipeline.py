from __future__ import annotations

import argparse
from datetime import datetime, timezone

from trading_bot.crisis_radar.evidence_memory import split_evidence
from trading_bot.crisis_radar.postgres_memory import PostgresEvidenceMemory, connect_postgres


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify advanced Crisis Radar evidence memory")
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    document_id = 9_223_372_036_854_000_001
    memory = PostgresEvidenceMemory(args.dsn)
    now = datetime.now(timezone.utc)
    payload = {
        "id": document_id,
        "source_code": "integration_contract",
        "source_tier": "A",
        "publisher": "Local integration test",
        "published_at": now,
        "fetched_at": now,
        "title": "Crisis radar pgvector integration evidence",
        "body": "A deterministic lexical probe with a relational evidence identifier.",
        "url": "https://example.invalid/local-contract",
        "original_language": "en",
        "content_hash": "integration-contract-v1",
        "metadata": {"temporary": True},
    }
    try:
        memory.upsert_document(payload)
        chunks = split_evidence(document_id, f"{payload['title']}\n{payload['body']}")
        written = memory.upsert_chunks(chunks)
        memory.enqueue_embedding(document_id, error_code="integration_probe")
        hits = memory.hybrid_search("deterministic lexical probe", limit=5)
        health = memory.health()
        assert written == len(chunks)
        assert any(hit.document_id == document_id for hit in hits)
        assert health["ready"] is True
        assert health["embedding_queue_depth"] >= 1
        print(
            {
                "ok": True,
                "profile": "advanced-local",
                "chunks_written": written,
                "search_evidence_id": document_id,
                "health_ready": health["ready"],
            }
        )
    finally:
        with connect_postgres(args.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM crisis_radar_memory.documents WHERE id=%s",
                    (document_id,),
                )


if __name__ == "__main__":
    main()
