-- ============================================================================
-- SNP Memory System — V2 PostgreSQL 16 + pgvector Initialization Script
-- ============================================================================
-- Architecture:
--   1. pgvector (1024 dimensions) for Dense Semantic Retrieval (bge-m3 / text-embedding-3-small)
--   2. Native PostgreSQL Full-Text Search (tsvector + GIN) for Keyword Recall (Hybrid Search)
--   3. Fail-Closed Kernel Row-Level Security (RLS) enforcing Department Clearance sets
--   4. Restricted runtime application role: `rag_app_role` (NOSUPERUSER, NOBYPASSRLS)
-- ============================================================================

-- 1. Enable Required Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. Master Documents Registry
CREATE TABLE IF NOT EXISTS rag_documents (
    doc_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_uri      TEXT UNIQUE NOT NULL,
    allowed_depts   TEXT[] NOT NULL CHECK (cardinality(allowed_depts) > 0),
    title           TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Document Chunks (Hybrid Dense Vector + Sparse Full-Text Search)
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

-- 4. High-Performance Indexes
-- HNSW Vector Index (Cosine Distance Metric)
CREATE INDEX IF NOT EXISTS rag_chunks_hnsw_idx 
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);

-- GIN Full-Text Search Index (BM25 / Keyword Retrieval)
CREATE INDEX IF NOT EXISTS rag_chunks_tsv_idx 
    ON rag_chunks USING gin (tsv);

-- B-Tree Indexes for Foreign Keys and URI Lookup
CREATE INDEX IF NOT EXISTS rag_docs_uri_idx 
    ON rag_documents (source_uri);

CREATE INDEX IF NOT EXISTS rag_chunks_doc_id_idx 
    ON rag_chunks (doc_id);

-- 5. Restricted Application Role Provisioning
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rag_app_role') THEN
        CREATE ROLE rag_app_role WITH LOGIN PASSWORD 'rag_app_secret' NOSUPERUSER NOBYPASSRLS NOCREATEDB;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO rag_app_role;
GRANT SELECT ON rag_documents, rag_chunks TO rag_app_role;

-- 6. Row-Level Security (RLS) Configuration
-- Kernel-enforced department isolation (Fail-Closed)
ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_chunks FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS dept_overlap_policy ON rag_chunks;

CREATE POLICY dept_overlap_policy ON rag_chunks
FOR SELECT TO rag_app_role
USING (
    (SELECT d.allowed_depts FROM rag_documents d WHERE d.doc_id = rag_chunks.doc_id)
    && string_to_array(NULLIF(current_setting('scout.current_depts', true), ''), ',')
);
