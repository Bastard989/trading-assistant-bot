from __future__ import annotations

import argparse
import asyncio
import json
import os

from dotenv import load_dotenv

from trading_bot.crisis_radar.evidence_memory import EMBEDDING_MODEL, EvidenceChunk, OllamaEmbeddingClient
from trading_bot.crisis_radar.postgres_memory import PostgresEvidenceMemory, connect_postgres


async def rebuild(dsn: str, *, base_url: str, model: str, batch_size: int = 16) -> dict:
    if batch_size < 1 or batch_size > 32:
        raise ValueError("batch size must be between 1 and 32")
    with connect_postgres(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT document_id, ordinal, text, content_hash
                FROM crisis_radar_memory.chunks ORDER BY document_id, ordinal
                """
            )
            chunks = tuple(EvidenceChunk(int(a), int(b), str(c), str(d)) for a, b, c, d in cursor.fetchall())
    provider = OllamaEmbeddingClient(base_url=base_url, model=model)
    memory = PostgresEvidenceMemory(dsn)
    written = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        embeddings = await provider.embed(tuple(item.text for item in batch))
        written += memory.upsert_chunks(batch, embeddings=embeddings, embedding_model=model)
    return {"model": model, "chunk_count": len(chunks), "embedded_count": written, "rebuildable": True}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.getenv("CRISIS_POSTGRES_DSN", ""))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--model", default=os.getenv("CRISIS_EMBEDDING_MODEL", EMBEDDING_MODEL))
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("CRISIS_POSTGRES_DSN or --dsn is required")
    result = asyncio.run(
        rebuild(args.dsn, base_url=args.ollama_url, model=args.model, batch_size=args.batch_size)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
