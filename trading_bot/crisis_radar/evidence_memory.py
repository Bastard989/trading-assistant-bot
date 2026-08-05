from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

import httpx


EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_DIMENSIONS = 768


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


@dataclass(frozen=True)
class EvidenceChunk:
    document_id: int
    ordinal: int
    text: str
    content_hash: str


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    document_id: int
    text: str
    source_url: str
    published_at: datetime
    lexical_rank: int | None
    vector_rank: int | None
    fused_score: Decimal


def split_evidence(document_id: int, text: str, *, max_chars: int = 1200) -> tuple[EvidenceChunk, ...]:
    if document_id < 1 or max_chars < 200 or max_chars > 4000:
        raise ValueError("invalid evidence chunk parameters")
    paragraphs = [" ".join(part.split()) for part in text.split("\n") if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            pieces = [paragraph[index : index + max_chars] for index in range(0, len(paragraph), max_chars)]
        else:
            pieces = [paragraph]
        for piece in pieces:
            candidate = f"{current}\n{piece}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return tuple(
        EvidenceChunk(
            document_id=document_id,
            ordinal=index,
            text=chunk,
            content_hash=hashlib.sha256(chunk.encode()).hexdigest(),
        )
        for index, chunk in enumerate(chunks)
    )


def reciprocal_rank_fusion(
    lexical_ids: tuple[int, ...],
    vector_ids: tuple[int, ...],
    *,
    k: int = 60,
) -> tuple[tuple[int, Decimal], ...]:
    if k < 1:
        raise ValueError("RRF k must be positive")
    scores: dict[int, Decimal] = {}
    for ranking in (lexical_ids, vector_ids):
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, Decimal("0")) + Decimal(1) / Decimal(k + rank)
    return tuple(sorted(scores.items(), key=lambda pair: (-pair[1], pair[0])))


class OllamaEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        model: str = EMBEDDING_MODEL,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client
        self.timeout_seconds = timeout_seconds

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts or len(texts) > 32 or any(not text.strip() or len(text) > 8000 for text in texts):
            raise ValueError("embedding batch must contain 1-32 bounded texts")
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            try:
                response = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": list(texts), "truncate": True},
                )
            except httpx.RequestError as exc:
                raise EmbeddingError("local embedding service is unavailable") from exc
            if response.status_code != 200:
                raise EmbeddingError(f"local embedding service returned HTTP {response.status_code}")
            try:
                values = response.json()["embeddings"]
                embeddings = tuple(tuple(float(value) for value in row) for row in values)
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError("invalid local embedding response") from exc
            if len(embeddings) != len(texts) or any(len(row) != EMBEDDING_DIMENSIONS for row in embeddings):
                raise EmbeddingError("unexpected local embedding dimensions")
            return embeddings
        finally:
            if owns_client:
                await client.aclose()


def grounded_answer_contract(*, query: str, evidence_ids: tuple[int, ...], claims: tuple[dict, ...]) -> str:
    if not query.strip() or not evidence_ids:
        raise ValueError("grounded answer requires a query and evidence IDs")
    allowed = set(evidence_ids)
    for claim in claims:
        cited = claim.get("evidence_ids")
        if not isinstance(cited, list) or not cited or not set(cited) <= allowed:
            raise ValueError("every claim must cite retrieved evidence IDs")
        if not isinstance(claim.get("text"), str) or not claim["text"].strip():
            raise ValueError("grounded claim text is required")
    payload = {
        "query": query,
        "evidence_ids": evidence_ids,
        "claims": claims,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": "evidence-grounded-v1",
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
