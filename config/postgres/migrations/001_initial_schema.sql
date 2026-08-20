-- 001_initial_schema.sql — Core tables and indexes for SNP Data Vault RAG

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Master Documents Registry
CREATE TABLE IF NOT EXISTS rag_documents (
    doc_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_uri      TEXT UNIQUE NOT NULL,
    allowed_depts   TEXT[] NOT NULL CHECK (cardinality(allowed_depts) > 0),
    title           TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Document Chunks (Hybrid Dense Vector + Sparse Full-Text Search)
CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id          UUID NOT NULL REFERENCES rag_documents(doc_id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    chunk_text      TEXT NOT NULL,
    context_prefix  TEXT,
    tsv             tsvector GENERATED ALWAYS AS (
                        to_tsvector('english', coalesce(context_prefix, '') || ' ' || chunk_text)
                    ) STORED,
    embedding       vector(1024),
    metadata        JSONB DEFAULT '{}'::jsonb
);

-- High-Performance Indexes
CREATE INDEX IF NOT EXISTS rag_chunks_hnsw_idx 
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS rag_chunks_tsv_idx 
    ON rag_chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS rag_docs_uri_idx 
    ON rag_documents (source_uri);

CREATE INDEX IF NOT EXISTS rag_chunks_doc_id_idx 
    ON rag_chunks (doc_id);
