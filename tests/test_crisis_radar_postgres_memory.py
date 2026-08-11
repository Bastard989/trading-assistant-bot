from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from trading_bot.crisis_radar.evidence_memory import EvidenceChunk
from trading_bot.crisis_radar.postgres_memory import PostgresEvidenceMemory, connect_postgres


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, *, one=None, many=None) -> None:
        self.one = one
        self.many = [] if many is None else many
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, parameters=None) -> None:
        self.executions.append((query, parameters))

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.fake_cursor


def connector(cursor: FakeCursor):
    def connect(dsn: str):
        assert dsn == "postgresql://memory"
        return FakeConnection(cursor)

    return connect


def document_payload() -> dict:
    return {
        "id": 7,
        "source_code": "fed_news",
        "source_tier": "A",
        "publisher": "Federal Reserve",
        "published_at": NOW,
        "fetched_at": NOW,
        "title": "Financial stability release",
        "body": "Official evidence body",
        "url": "https://www.federalreserve.gov/example",
        "original_language": "en",
        "content_hash": "a" * 64,
        "metadata": {"region": "US"},
    }


def test_connect_postgres_validation_and_driver(monkeypatch) -> None:
    with pytest.raises(ValueError, match="DSN"):
        connect_postgres(" ")

    captured = []
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=lambda dsn: captured.append(dsn) or "connection"),
    )
    assert connect_postgres("postgresql://memory") == "connection"
    assert captured == ["postgresql://memory"]


def test_document_upsert_validates_contract_and_serializes_metadata() -> None:
    cursor = FakeCursor()
    memory = PostgresEvidenceMemory("postgresql://memory", connect=connector(cursor))

    with pytest.raises(ValueError, match="missing required"):
        memory.upsert_document({"id": 1})
    memory.upsert_document(document_payload())

    assert len(cursor.executions) == 1
    parameters = cursor.executions[0][1]
    assert parameters[0] == 7
    assert parameters[-1] == '{"region": "US"}'


def test_chunk_upsert_supports_lexical_only_and_embeddings() -> None:
    chunks = (
        EvidenceChunk(document_id=7, ordinal=0, text="first", content_hash="b" * 64),
        EvidenceChunk(document_id=7, ordinal=1, text="second", content_hash="c" * 64),
    )
    cursor = FakeCursor()
    memory = PostgresEvidenceMemory("postgresql://memory", connect=connector(cursor))

    with pytest.raises(ValueError, match="counts differ"):
        memory.upsert_chunks(chunks, embeddings=((0.1, 0.2),))
    assert memory.upsert_chunks(chunks) == 2
    assert memory.upsert_chunks(
        chunks,
        embeddings=((0.1, 0.2), (0.3, 0.4)),
        embedding_model="test-embed",
    ) == 2

    lexical_parameters = cursor.executions[0][1]
    embedded_parameters = cursor.executions[2][1]
    assert lexical_parameters[4] is None
    assert embedded_parameters[4] == "[0.1,0.2]"
    assert embedded_parameters[5] == "test-embed"
    assert embedded_parameters[6].tzinfo is timezone.utc


def test_embedding_queue_truncates_error_and_health_handles_empty_and_populated() -> None:
    queue_cursor = FakeCursor()
    memory = PostgresEvidenceMemory("postgresql://memory", connect=connector(queue_cursor))
    memory.enqueue_embedding(7, error_code="x" * 200)
    assert queue_cursor.executions[0][1] == (7, "x" * 120)

    empty = PostgresEvidenceMemory(
        "postgresql://memory", connect=connector(FakeCursor(one=(0, 0, 0, 0, None)))
    ).health()
    populated = PostgresEvidenceMemory(
        "postgresql://memory", connect=connector(FakeCursor(one=(3, 10, 8, 2, NOW)))
    ).health()

    assert empty["embedding_coverage"] == 0
    assert empty["last_document_at"] is None
    assert populated == {
        "ready": True,
        "documents": 3,
        "chunks": 10,
        "embedded_chunks": 8,
        "embedding_coverage": 0.8,
        "embedding_queue_depth": 2,
        "last_document_at": NOW.isoformat(),
    }


def test_hybrid_search_validates_query_fuses_ranks_and_limits() -> None:
    rows = [
        (1, 10, "lexical", "https://source/1", NOW, 1, None),
        (2, 11, "both", "https://source/2", NOW, 2, 1),
        (3, 12, "vector", "https://source/3", NOW, None, 2),
    ]
    cursor = FakeCursor(many=rows)
    memory = PostgresEvidenceMemory("postgresql://memory", connect=connector(cursor))

    with pytest.raises(ValueError, match="invalid"):
        memory.hybrid_search(" ")
    with pytest.raises(ValueError, match="invalid"):
        memory.hybrid_search("stress", limit=51)
    hits = memory.hybrid_search(
        "bank stress",
        query_embedding=(0.1, 0.2),
        limit=2,
        published_after=NOW,
        regions=("US",),
    )

    assert [hit.chunk_id for hit in hits] == [2, 1]
    assert hits[0].document_id == 11
    assert hits[0].lexical_rank == 2
    assert hits[0].vector_rank == 1
    parameters = cursor.executions[0][1]
    assert parameters[0:2] == ("bank stress", "bank stress")
    assert parameters[4] == "[0.1,0.2]"
