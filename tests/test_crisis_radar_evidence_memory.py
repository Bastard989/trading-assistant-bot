import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.evidence_memory import (
    EMBEDDING_DIMENSIONS,
    EmbeddingError,
    OllamaEmbeddingClient,
    grounded_answer_contract,
    reciprocal_rank_fusion,
    split_evidence,
)
from trading_bot.crisis_radar.evidence_pipeline import AdvancedEvidencePipeline
from trading_bot.crisis_radar.news import RssAdapter
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


def test_evidence_chunks_are_rebuildable_and_content_addressed() -> None:
    chunks = split_evidence(42, "First evidence paragraph.\nSecond evidence paragraph.", max_chars=200)
    repeated = split_evidence(42, "First evidence paragraph.\nSecond evidence paragraph.", max_chars=200)

    assert chunks == repeated
    assert chunks[0].document_id == 42
    assert len(chunks[0].content_hash) == 64


def test_rrf_combines_lexical_and_vector_without_turning_similarity_into_fact() -> None:
    ranking = reciprocal_rank_fusion((1, 2, 3), (3, 2, 4))

    assert ranking[0][0] in {2, 3}
    assert {item_id for item_id, _ in ranking} == {1, 2, 3, 4}
    assert all(score > Decimal("0") for _, score in ranking)


def test_grounded_contract_rejects_uncited_claims() -> None:
    with pytest.raises(ValueError, match="every claim"):
        grounded_answer_contract(
            query="What changed?",
            evidence_ids=(10,),
            claims=({"text": "Unsupported", "evidence_ids": [11]},),
        )

    payload = json.loads(
        grounded_answer_contract(
            query="What changed?",
            evidence_ids=(10,),
            claims=({"text": "Supported", "evidence_ids": [10]},),
        )
    )
    assert payload["contract"] == "evidence-grounded-v1"


def test_ollama_embedding_contract_validates_dimensions() -> None:
    async def valid(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[0.0] * EMBEDDING_DIMENSIONS]})

    async def scenario(handler) -> tuple[tuple[float, ...], ...]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OllamaEmbeddingClient(client=client).embed(("evidence",))

    assert len(asyncio.run(scenario(valid))[0]) == EMBEDDING_DIMENSIONS

    async def invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.0, 1.0]]})

    with pytest.raises(EmbeddingError, match="dimensions"):
        asyncio.run(scenario(invalid))


def test_postgres_schema_keeps_vectors_derived_from_relational_documents() -> None:
    schema = Path("deploy/postgres/crisis_radar_memory.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS crisis_radar_memory.documents" in schema
    assert "embedding VECTOR(768)" in schema
    assert "document_id BIGINT NOT NULL REFERENCES" in schema
    assert "shadow_rows" in schema
    assert "embedding_queue" in schema


def test_basic_profile_searches_relational_evidence_without_embeddings(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "memory.sqlite3"))
    CrisisRadarService(repository).bootstrap()
    now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    payload = (Path("tests/fixtures/fed_monetary_news.xml")).read_bytes()
    items = RssAdapter("fed_news").normalize(payload, fetched_at=now)
    evidence_id = repository.save_news_item(items[-1]).news_item_id

    result = repository.search_evidence_basic("economic projections", limit=5)

    assert result["profile"] == "basic-local"
    assert result["items"][0]["evidence_id"] == evidence_id
    assert result["items"][0]["source_code"] == "fed_news"


def test_advanced_pipeline_continuously_ingests_relational_document_and_embeddings() -> None:
    class Memory:
        def __init__(self) -> None:
            self.documents = []
            self.chunks = []
            self.queued = []

        def upsert_document(self, payload) -> None:
            self.documents.append(payload)

        def upsert_chunks(self, chunks, *, embeddings=None, embedding_model=None) -> int:
            self.chunks.append((chunks, embeddings, embedding_model))
            return len(chunks)

        def enqueue_embedding(self, document_id, *, error_code="") -> None:
            self.queued.append((document_id, error_code))

        def health(self) -> dict:
            return {"ready": True, "documents": len(self.documents)}

    class Embeddings:
        async def embed(self, texts):
            return tuple((0.0,) * EMBEDDING_DIMENSIONS for _ in texts)

    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    item = RssAdapter("fed_news").normalize(
        Path("tests/fixtures/fed_monetary_news.xml").read_bytes(), fetched_at=now
    )[-1]
    memory = Memory()
    pipeline = AdvancedEvidencePipeline(memory, embedding_provider=Embeddings())

    result = asyncio.run(pipeline.ingest_news(77, item))

    assert result == {
        "profile": "advanced-local",
        "document_id": 77,
        "chunks": 1,
        "embedded": True,
        "queued": False,
    }
    assert memory.documents[0]["metadata"]["relational_evidence_id"] == 77
    assert len(memory.chunks[0][1][0]) == EMBEDDING_DIMENSIONS
    assert not memory.queued


def test_advanced_pipeline_queues_embeddings_without_discarding_searchable_text() -> None:
    class Memory:
        def __init__(self) -> None:
            self.queued = []
            self.chunks = []

        def upsert_document(self, payload) -> None:
            self.document = payload

        def upsert_chunks(self, chunks, *, embeddings=None, embedding_model=None) -> int:
            self.chunks.extend(chunks)
            assert embeddings is None
            return len(chunks)

        def enqueue_embedding(self, document_id, *, error_code="") -> None:
            self.queued.append((document_id, error_code))

    now = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    item = RssAdapter("fed_news").normalize(
        Path("tests/fixtures/fed_monetary_news.xml").read_bytes(), fetched_at=now
    )[-1]
    memory = Memory()
    pipeline = AdvancedEvidencePipeline(memory, embedding_provider=None)

    result = asyncio.run(pipeline.ingest_news(78, item))

    assert result["queued"] is True
    assert memory.queued == [(78, "embedding_provider_disabled")]
    assert memory.chunks
