-- Correct installations that applied the early 002 draft: runtime roles are
-- always non-privileged and ingestion receives explicit full-corpus DML RLS.

ALTER ROLE rag_app_role NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
ALTER ROLE rag_ingest_role NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;

DROP POLICY IF EXISTS ingest_all_documents ON rag_documents;
CREATE POLICY ingest_all_documents ON rag_documents
    FOR ALL TO rag_ingest_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS ingest_all_chunks ON rag_chunks;
CREATE POLICY ingest_all_chunks ON rag_chunks
    FOR ALL TO rag_ingest_role
    USING (true)
    WITH CHECK (true);
