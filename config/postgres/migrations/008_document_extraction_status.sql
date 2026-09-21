-- Record what each document's last ingest actually extracted from its bytes.
--
-- `capability_fingerprint` (004) answers "what could this environment do?".
-- It cannot answer "did it?", and must not be made to: it records what was
-- AVAILABLE, never an outcome, so that a parser which could not look cannot
-- claim it looked (T5.1). The figure extractor was available and worked on
-- 2026-09-15; what failed was the remote describer, which is not part of the
-- environment. Two runs of the same environment must produce the same
-- fingerprint, so the outcome needs its own column.
--
-- Why a column and not only chunk metadata: chunk metadata is per chunk and
-- carries the state only for documents that produced chunks, and it is written
-- as part of retrieval payloads rather than as a record about the document. A
-- document is the thing whose extraction succeeded or failed.
--
-- Additive and nullable on purpose: every document indexed before this exists
-- has no recorded outcome, and "not recorded" must read as unknown rather than
-- as healthy. `verify-extraction` reports those separately for that reason.

ALTER TABLE rag_documents
    ADD COLUMN IF NOT EXISTS extraction_status jsonb;

COMMENT ON COLUMN rag_documents.extraction_status IS
    'Outcome of the last ingest''s structural extraction: per-extractor state '
    '(figures, tables), how many figures were found and described, and whether '
    'the document carries all the evidence its bytes contain. An OUTCOME, '
    'deliberately separate from capability_fingerprint, which records only '
    'what the environment could do.';

-- Documents whose extraction is incomplete are the query this exists to serve,
-- and they are a small minority, so the index is partial.
CREATE INDEX IF NOT EXISTS rag_documents_extraction_incomplete_idx
    ON rag_documents ((extraction_status->>'complete'))
    WHERE extraction_status IS NOT NULL
      AND extraction_status->>'complete' <> 'true';
