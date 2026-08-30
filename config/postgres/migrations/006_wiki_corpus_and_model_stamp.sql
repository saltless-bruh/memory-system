-- Make corpus and page class indexable, ahead of wiki bodies entering rag_chunks.
--
-- V3 moves retrieval so the index finds and the vault answers, which means wiki
-- page bodies are chunked into the same `rag_chunks` table that already holds
-- raw evidence. Two predicates become load-bearing the moment that happens, and
-- neither is indexable today:
--
--   1. Corpus. `rag_documents.source_uri` is a single flat namespace, and the
--      only filter the query path offers is exact equality:
--
--          backends/pgvector.py:131  WHERE ($2::text IS NULL OR d.source_uri = $2::text)
--          backends/pgvector.py:141  (same predicate on the text arm)
--
--      There is no prefix or corpus predicate, so "search the wiki, not the raw
--      evidence" has nothing to filter on. Without this, the two corpora are
--      permanently entangled in one result set.
--
--   2. Page class. Blueprint V3 §5.6 expresses preference between document
--      classes *inside* the rank fusion, by varying RRF's `k` per row from the
--      page's `type`. That constant is inlined at backends/pgvector.py:150
--      today. Once it is a per-row lookup on `metadata->>'type'`, every
--      candidate row evaluates it, and an unindexed expression turns the
--      fusion CTE into a sequential scan over the chunk table.
--
-- Deliberately index-only. The keys these indexes cover -- corpus, type, model,
-- dim, role, tldr_source, wikilinks, sha256 -- all live inside the existing
-- `metadata` JSONB column, so no column DDL and no table rewrite is required,
-- and every chunk indexed before this migration stays readable with those keys
-- simply absent. An absent key must degrade, never refuse: bricking a populated
-- index on upgrade would be a worse failure than the missing predicate.
--
-- Plain CREATE INDEX, not CONCURRENTLY, because scripts/migrate_postgres.py
-- runs each migration file inside a single transaction and CONCURRENTLY cannot
-- run there. That takes a brief ACCESS EXCLUSIVE lock, which is the correct
-- trade at this corpus size -- the reference vault is ~1,500 chunks, where the
-- build is milliseconds. Revisit if the table reaches the ~1M-chunk projection.

-- Corpus separation: which body of material a chunk came from ('wiki' | 'raw').
CREATE INDEX IF NOT EXISTS rag_chunks_corpus_idx
    ON rag_chunks ((metadata->>'corpus'));

-- Page class, mirrored from the page's `type:` frontmatter. Drives the per-class
-- RRF weight in V3 §5.6, which is what actually settles D-7; the superseded
-- "rank curated above raw at equal score" rule never fired, because fused ranks
-- are effectively never equal.
CREATE INDEX IF NOT EXISTS rag_chunks_type_idx
    ON rag_chunks ((metadata->>'type'));

COMMENT ON INDEX rag_chunks_corpus_idx IS
    'V3: separates wiki-body chunks from raw-evidence chunks in the shared table.';

COMMENT ON INDEX rag_chunks_type_idx IS
    'V3 §5.6: per-class RRF weight lookup; curated types outrank raw at comparable rank.';
