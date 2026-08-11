from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import datetime
from typing import Protocol

from trading_bot.crisis_radar.evidence_memory import (
    EMBEDDING_MODEL,
    EmbeddingError,
    EmbeddingProvider,
    OllamaEmbeddingClient,
    split_evidence,
)
from trading_bot.crisis_radar.news import NewsItem
from trading_bot.crisis_radar.postgres_memory import PostgresEvidenceMemory


class EvidencePipeline(Protocol):
    profile: str

    async def ingest_news(self, evidence_id: int, item: NewsItem) -> dict: ...

    def search(
        self, query: str, *, limit: int, published_after: datetime | None = None
    ) -> dict: ...

    def health(self) -> dict: ...


class AdvancedEvidencePipeline:
    profile = "advanced-local"

    def __init__(
        self,
        memory: PostgresEvidenceMemory,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_model: str = EMBEDDING_MODEL,
    ) -> None:
        self.memory = memory
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model

    async def ingest_news(self, evidence_id: int, item: NewsItem) -> dict:
        body = item.summary or item.evidence_excerpt or item.title
        payload = {
            "id": evidence_id,
            "source_code": item.source_code,
            "source_tier": item.source_tier,
            "publisher": item.publisher or item.source_code,
            "published_at": item.published_at,
            "fetched_at": item.fetched_at,
            "title": item.title,
            "body": body,
            "url": item.url,
            "original_language": item.original_language,
            "content_hash": item.content_hash,
            "metadata": {
                "provider_item_id": item.provider_item_id,
                "category": item.category,
                "importance": item.importance,
                "relational_evidence_id": evidence_id,
            },
        }
        chunks = split_evidence(evidence_id, f"{item.title}\n{body}")
        await asyncio.to_thread(self.memory.upsert_document, payload)
        if not chunks:
            return {"profile": self.profile, "document_id": evidence_id, "chunks": 0}
        embeddings = None
        queued = False
        if self.embedding_provider is not None:
            try:
                embeddings = await self.embedding_provider.embed(
                    tuple(chunk.text for chunk in chunks)
                )
            except EmbeddingError as exc:
                queued = True
                await asyncio.to_thread(
                    self.memory.enqueue_embedding,
                    evidence_id,
                    error_code=type(exc).__name__,
                )
        else:
            queued = True
            await asyncio.to_thread(
                self.memory.enqueue_embedding,
                evidence_id,
                error_code="embedding_provider_disabled",
            )
        written = await asyncio.to_thread(
            self.memory.upsert_chunks,
            chunks,
            embeddings=embeddings,
            embedding_model=self.embedding_model if embeddings is not None else None,
        )
        return {
            "profile": self.profile,
            "document_id": evidence_id,
            "chunks": written,
            "embedded": embeddings is not None,
            "queued": queued,
        }

    def search(
        self, query: str, *, limit: int, published_after: datetime | None = None
    ) -> dict:
        hits = self.memory.hybrid_search(
            query, limit=limit, published_after=published_after
        )
        return {
            "profile": self.profile,
            "query": query,
            "items": [
                {
                    **asdict(hit),
                    "published_at": hit.published_at.isoformat()
                    if isinstance(hit.published_at, datetime)
                    else str(hit.published_at),
                    "fused_score": format(hit.fused_score, "f"),
                    "evidence_id": hit.document_id,
                }
                for hit in hits
            ],
        }

    def health(self) -> dict:
        return {"profile": self.profile, **self.memory.health()}


def build_evidence_pipeline_from_environment() -> AdvancedEvidencePipeline | None:
    profile = os.getenv("CRISIS_EVIDENCE_PROFILE", "basic-local").strip().lower()
    if profile == "basic-local":
        return None
    if profile != "advanced-local":
        raise ValueError("CRISIS_EVIDENCE_PROFILE must be basic-local or advanced-local")
    dsn = os.getenv("CRISIS_POSTGRES_DSN", "").strip()
    if not dsn:
        raise ValueError("CRISIS_POSTGRES_DSN is required for advanced-local evidence memory")
    embeddings_enabled = os.getenv("CRISIS_EMBEDDINGS_ENABLED", "true").lower() in {
        "1", "true", "yes", "on"
    }
    provider = (
        OllamaEmbeddingClient(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            model=os.getenv("CRISIS_EMBEDDING_MODEL", EMBEDDING_MODEL),
        )
        if embeddings_enabled
        else None
    )
    return AdvancedEvidencePipeline(
        PostgresEvidenceMemory(dsn),
        embedding_provider=provider,
        embedding_model=os.getenv("CRISIS_EMBEDDING_MODEL", EMBEDDING_MODEL),
    )
