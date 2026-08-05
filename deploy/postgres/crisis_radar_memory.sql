CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS crisis_radar_memory;

CREATE TABLE IF NOT EXISTS crisis_radar_memory.documents (
    id BIGINT PRIMARY KEY,
    source_code TEXT NOT NULL,
    source_tier TEXT NOT NULL CHECK (source_tier IN ('A', 'B', 'C')),
    publisher TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    url TEXT NOT NULL,
    original_language TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(body, '')), 'B')
    ) STORED
);

CREATE TABLE IF NOT EXISTS crisis_radar_memory.events (
    id BIGINT PRIMARY KEY,
    taxonomy TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    regions JSONB NOT NULL DEFAULT '[]'::jsonb,
    entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    assets JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence NUMERIC NOT NULL,
    event_score NUMERIC NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS crisis_radar_memory.chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES crisis_radar_memory.documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding VECTOR(768),
    embedding_model TEXT,
    embedded_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text, ''))) STORED,
    UNIQUE(document_id, ordinal, content_hash)
);

CREATE TABLE IF NOT EXISTS crisis_radar_memory.edges (
    id BIGSERIAL PRIMARY KEY,
    from_kind TEXT NOT NULL,
    from_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    to_kind TEXT NOT NULL,
    to_id TEXT NOT NULL,
    evidence_document_id BIGINT REFERENCES crisis_radar_memory.documents(id) ON DELETE SET NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    confidence NUMERIC NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(from_kind, from_id, relation, to_kind, to_id, evidence_document_id)
);

CREATE TABLE IF NOT EXISTS crisis_radar_memory.shadow_rows (
    table_name TEXT NOT NULL,
    row_key TEXT NOT NULL,
    row_payload JSONB NOT NULL,
    row_hash TEXT NOT NULL,
    migrated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(table_name, row_key)
);

CREATE TABLE IF NOT EXISTS crisis_radar_memory.migration_manifests (
    id BIGSERIAL PRIMARY KEY,
    sqlite_path_hash TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    source_counts JSONB NOT NULL,
    target_counts JSONB NOT NULL,
    source_checksum TEXT NOT NULL,
    target_checksum TEXT NOT NULL,
    parity BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS cr_memory_documents_search
    ON crisis_radar_memory.documents USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS cr_memory_chunks_search
    ON crisis_radar_memory.chunks USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS cr_memory_chunks_embedding
    ON crisis_radar_memory.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS cr_memory_events_time
    ON crisis_radar_memory.events(last_seen_at DESC, taxonomy, status);
CREATE INDEX IF NOT EXISTS cr_memory_edges_from
    ON crisis_radar_memory.edges(from_kind, from_id, relation);
