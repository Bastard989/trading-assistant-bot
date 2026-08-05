import asyncio
import json
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
