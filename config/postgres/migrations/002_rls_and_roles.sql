-- Application roles are created without login credentials.  The separate
-- provisioning step enables LOGIN with secret-backed passwords.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rag_app_role') THEN
        CREATE ROLE rag_app_role NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rag_ingest_role') THEN
        CREATE ROLE rag_ingest_role NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END $$;

ALTER ROLE rag_app_role NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
ALTER ROLE rag_ingest_role NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;

GRANT USAGE ON SCHEMA public TO rag_app_role, rag_ingest_role;
GRANT SELECT ON rag_documents, rag_chunks TO rag_app_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON rag_documents, rag_chunks TO rag_ingest_role;

ALTER TABLE rag_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_documents FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_chunks FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS doc_dept_overlap_policy ON rag_documents;
DROP POLICY IF EXISTS doc_dept_overlap_select ON rag_documents;
CREATE POLICY doc_dept_overlap_select ON rag_documents
    FOR SELECT TO rag_app_role
    USING (
        NULLIF(current_setting('scout.current_depts', true), '') IS NOT NULL
        AND (
            'all' = ANY(allowed_depts)
            OR allowed_depts && string_to_array(
                current_setting('scout.current_depts', true), ','
            )
        )
    );

DROP POLICY IF EXISTS dept_overlap_policy ON rag_chunks;
DROP POLICY IF EXISTS chunk_dept_overlap_select ON rag_chunks;
CREATE POLICY chunk_dept_overlap_select ON rag_chunks
    FOR SELECT TO rag_app_role
    USING (
        NULLIF(current_setting('scout.current_depts', true), '') IS NOT NULL
        AND EXISTS (
            SELECT 1
            FROM rag_documents AS document
            WHERE document.doc_id = rag_chunks.doc_id
              AND (
                  'all' = ANY(document.allowed_depts)
                  OR document.allowed_depts && string_to_array(
                      current_setting('scout.current_depts', true), ','
                  )
              )
        )
    );
