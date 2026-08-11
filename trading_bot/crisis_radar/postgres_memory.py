from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

from trading_bot.crisis_radar.evidence_memory import EvidenceChunk, SearchHit, reciprocal_rank_fusion


class PostgresMemoryUnavailable(RuntimeError):
    pass


def connect_postgres(dsn: str):
    if not dsn.strip():
        raise ValueError("PostgreSQL DSN is required")
    try:
        import psycopg
    except ImportError as exc:
        raise PostgresMemoryUnavailable("psycopg is not installed") from exc
    return psycopg.connect(dsn)


class PostgresEvidenceMemory:
    def __init__(self, dsn: str, *, connect: Callable | None = None) -> None:
        self.dsn = dsn
        self._connect = connect or connect_postgres

    def upsert_document(self, payload: dict) -> None:
        required = {
            "id", "source_code", "source_tier", "publisher", "published_at", "fetched_at",
            "title", "body", "url", "original_language", "content_hash",
        }
        if not required <= payload.keys():
            raise ValueError("evidence document is missing required fields")
        with self._connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO crisis_radar_memory.documents(
                        id, source_code, source_tier, publisher, published_at, fetched_at,
                        title, body, url, original_language, content_hash, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT(id) DO UPDATE SET
                        source_code=excluded.source_code, source_tier=excluded.source_tier,
                        publisher=excluded.publisher, published_at=excluded.published_at,
                        fetched_at=excluded.fetched_at, title=excluded.title, body=excluded.body,
                        url=excluded.url, original_language=excluded.original_language,
                        content_hash=excluded.content_hash, metadata=excluded.metadata
                    """,
                    tuple(payload[key] for key in (
                        "id", "source_code", "source_tier", "publisher", "published_at",
                        "fetched_at", "title", "body", "url", "original_language", "content_hash",
                    )) + (json.dumps(payload.get("metadata", {}), sort_keys=True),),
                )

    def upsert_chunks(
        self,
        chunks: tuple[EvidenceChunk, ...],
        *,
        embeddings: tuple[tuple[float, ...], ...] | None = None,
        embedding_model: str | None = None,
    ) -> int:
        if embeddings is not None and len(embeddings) != len(chunks):
            raise ValueError("chunk and embedding counts differ")
        written = 0
        with self._connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                for index, chunk in enumerate(chunks):
                    vector = None if embeddings is None else "[" + ",".join(map(str, embeddings[index])) + "]"
                    cursor.execute(
                        """
                        INSERT INTO crisis_radar_memory.chunks(
                            document_id, ordinal, text, content_hash, embedding,
                            embedding_model, embedded_at
                        ) VALUES (%s, %s, %s, %s, %s::vector, %s, %s)
                        ON CONFLICT(document_id, ordinal, content_hash) DO UPDATE SET
                            text=excluded.text, embedding=excluded.embedding,
                            embedding_model=excluded.embedding_model, embedded_at=excluded.embedded_at
                        """,
                        (
                            chunk.document_id, chunk.ordinal, chunk.text, chunk.content_hash,
                            vector, embedding_model, datetime.now(timezone.utc) if vector is not None else None,
                        ),
                    )
                    written += 1
        return written

    def enqueue_embedding(self, document_id: int, *, error_code: str = "") -> None:
        with self._connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO crisis_radar_memory.embedding_queue(
                        document_id, status, attempts, last_error, available_at
                    ) VALUES (%s, 'pending', 0, %s, now())
                    ON CONFLICT(document_id) DO UPDATE SET
                        status='pending', last_error=excluded.last_error,
                        available_at=LEAST(
                            crisis_radar_memory.embedding_queue.available_at,
                            excluded.available_at
                        ), updated_at=now()
                    """,
                    (document_id, error_code[:120]),
                )

    def health(self) -> dict:
        with self._connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                      (SELECT count(*) FROM crisis_radar_memory.documents),
                      (SELECT count(*) FROM crisis_radar_memory.chunks),
                      (SELECT count(*) FROM crisis_radar_memory.chunks
                       WHERE embedding IS NOT NULL),
                      (SELECT count(*) FROM crisis_radar_memory.embedding_queue
                       WHERE status IN ('pending', 'retry')),
                      (SELECT max(fetched_at) FROM crisis_radar_memory.documents)
                    """
                )
                row = cursor.fetchone()
        documents = int(row[0])
        chunks = int(row[1])
        embedded = int(row[2])
        return {
            "ready": True,
            "documents": documents,
            "chunks": chunks,
            "embedded_chunks": embedded,
            "embedding_coverage": 0 if chunks == 0 else embedded / chunks,
            "embedding_queue_depth": int(row[3]),
            "last_document_at": None if row[4] is None else row[4].isoformat(),
        }

    def hybrid_search(
        self,
        query: str,
        *,
        query_embedding: tuple[float, ...] | None = None,
        limit: int = 10,
        published_after: datetime | None = None,
        regions: tuple[str, ...] = (),
    ) -> tuple[SearchHit, ...]:
        if not query.strip() or limit < 1 or limit > 50:
            raise ValueError("invalid hybrid search query")
        vector = None if query_embedding is None else "[" + ",".join(map(str, query_embedding)) + "]"
        with self._connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH lexical AS (
                        SELECT chunk.id, row_number() OVER (
                            ORDER BY ts_rank_cd(chunk.search_vector, websearch_to_tsquery('simple', %s)) DESC
                        ) AS rank
                        FROM crisis_radar_memory.chunks AS chunk
                        JOIN crisis_radar_memory.documents AS document ON document.id=chunk.document_id
                        WHERE chunk.search_vector @@ websearch_to_tsquery('simple', %s)
                          AND (%s::timestamptz IS NULL OR document.published_at >= %s)
                        LIMIT 100
                    ), vector_hits AS (
                        SELECT chunk.id, row_number() OVER (ORDER BY chunk.embedding <=> %s::vector) AS rank
                        FROM crisis_radar_memory.chunks AS chunk
                        JOIN crisis_radar_memory.documents AS document ON document.id=chunk.document_id
                        WHERE %s::vector IS NOT NULL AND chunk.embedding IS NOT NULL
                          AND (%s::timestamptz IS NULL OR document.published_at >= %s)
                        LIMIT 100
                    )
                    SELECT chunk.id, chunk.document_id, chunk.text, document.url,
                           document.published_at, lexical.rank, vector_hits.rank
                    FROM crisis_radar_memory.chunks AS chunk
                    JOIN crisis_radar_memory.documents AS document ON document.id=chunk.document_id
                    LEFT JOIN lexical ON lexical.id=chunk.id
                    LEFT JOIN vector_hits ON vector_hits.id=chunk.id
                    WHERE lexical.id IS NOT NULL OR vector_hits.id IS NOT NULL
                    LIMIT 200
                    """,
                    (query, query, published_after, published_after, vector, vector, published_after, published_after),
                )
                rows = cursor.fetchall()
        lexical_ids = tuple(int(row[0]) for row in sorted(rows, key=lambda row: row[5] or 10**9) if row[5])
        vector_ids = tuple(int(row[0]) for row in sorted(rows, key=lambda row: row[6] or 10**9) if row[6])
        fused = dict(reciprocal_rank_fusion(lexical_ids, vector_ids))
        hits = [
            SearchHit(
                chunk_id=int(row[0]), document_id=int(row[1]), text=row[2], source_url=row[3],
                published_at=row[4], lexical_rank=row[5], vector_rank=row[6], fused_score=fused[int(row[0])],
            )
            for row in rows
        ]
        return tuple(sorted(hits, key=lambda item: (-item.fused_score, item.chunk_id))[:limit])
