-- Record the parsing environment each document was ingested with.
--
-- Two runs of the same parser over the same bytes can produce different
-- corpora, because "the same parser" is not the same thing in two environments.
-- The host carries `pdfplumber` and Pillow; the deployed image deliberately
-- carries neither (T5.1). A host ingest therefore emits table sections
-- `sync-job` cannot produce and drops unannounced on its next pass — measured
-- 2026-08-26, chunks 127 -> 140 with three host-only table sections.
--
-- Additive and nullable on purpose: every document indexed before this exists
-- has no fingerprint, and an absent one must warn rather than refuse. Bricking a
-- working deployment on upgrade would be a worse failure than the drift.

ALTER TABLE rag_documents
    ADD COLUMN IF NOT EXISTS capability_fingerprint jsonb;

COMMENT ON COLUMN rag_documents.capability_fingerprint IS
    'Parsing environment at ingest: schema_version, parser_revision, python, and '
    'per-extractor probed availability. Records what was AVAILABLE, never what '
    'was configured -- a configured flag would repeat the T5.1 bug one layer up.';
