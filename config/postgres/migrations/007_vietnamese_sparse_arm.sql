-- 007_vietnamese_sparse_arm.sql — Add Vietnamese sparse retrieval via simple tokenizer
--
-- V3 retrieval reaches two arms: dense (vector) and sparse (full-text). The
-- sparse arm today uses only the English tsvector (`rag_chunks.tsv`), so
-- Vietnamese queries match nothing and fall back to vector-only retrieval.
--
-- This migration adds a second generated tsvector column using PostgreSQL's
-- `simple` configuration, which tokenizes text without language-specific
-- stemming. When given a Vietnamese query, the retrieval engine can query this
-- column (using OR fusion with the English column) to reach vocabulary the
-- English stemmer discarded.
--
-- The new column is generated and stored; PostgreSQL rewrites the table under
-- ACCESS EXCLUSIVE to compute initial values, but the operation is safe at
-- corpus scale (~2300 chunks) and every chunk indexed before this migration
-- stays readable with the new column present (no degradation).

-- Vietnamese full-text search column: simple tokenizer (no stemming).
-- Stored as a generated column for automatic updates on chunk_text or
-- context_prefix changes.
ALTER TABLE rag_chunks
ADD COLUMN IF NOT EXISTS tsv_simple tsvector GENERATED ALWAYS AS (
    to_tsvector('simple', coalesce(context_prefix, '') || ' ' || chunk_text)
) STORED;

-- GIN index for fast OR fusion in the sparse retrieval arm.
CREATE INDEX IF NOT EXISTS rag_chunks_tsv_simple_idx
    ON rag_chunks USING gin (tsv_simple);

COMMENT ON COLUMN rag_chunks.tsv_simple IS
    'V3: Vietnamese sparse retrieval via simple tokenizer (no stemming).';

COMMENT ON INDEX rag_chunks_tsv_simple_idx IS
    'V3: supports Vietnamese and other non-English full-text search queries.';
