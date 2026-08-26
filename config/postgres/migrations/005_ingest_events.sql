-- An append-only record of ingest events, enforced by the database.
--
-- The first design put "overrode the previous fingerprint" *inside* the
-- document's fingerprint column, which the next ingest overwrites: metadata,
-- not an audit trail. The second put it in a table and called it append-only
-- because no code updated it. Neither survives contact with the actual grants
-- in this schema:
--
--     002_rls_and_roles.sql:19  GRANT SELECT, INSERT, UPDATE, DELETE
--                                 ON rag_documents, rag_chunks TO rag_ingest_role;
--     003_ingest_role_rls.sql:8 CREATE POLICY ... FOR ALL TO rag_ingest_role
--                                 USING (true) WITH CHECK (true);
--
-- A table following the house pattern would be fully mutable by the exact
-- identity it audits. "No UPDATE path in the code" is a property of today's
-- code; this is a property of the database.

CREATE TABLE IF NOT EXISTS ingest_events (
    event_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at     timestamptz NOT NULL DEFAULT now(),

    -- The authenticated database identity, filled by PostgreSQL. NOT passed in
    -- by the caller: an environment variable naming the actor is a value the
    -- audited party chooses, which is not an audit identity.
    actor           text        NOT NULL DEFAULT session_user,

    -- Caller-supplied context (CI run id, operator note). UNVERIFIED by
    -- construction; kept separate from `actor` so the two are never confused.
    actor_hint      text,

    source_uri      text        NOT NULL,
    event_type      text        NOT NULL,
    old_fingerprint jsonb,
    new_fingerprint jsonb,

    -- The exact mismatch text the operator echoed back to authorise the
    -- override. Recording it proves *which* difference was acknowledged, so a
    -- stale acknowledgement cannot be reused for a later, different mismatch.
    acknowledgement text,

    CONSTRAINT ingest_events_type_known
        CHECK (event_type IN ('capability_override'))
);

COMMENT ON TABLE ingest_events IS
    'Append-only ingest audit. INSERT and SELECT only for rag_ingest_role; '
    'UPDATE/DELETE/TRUNCATE revoked and no RLS policy grants them. A superuser '
    'still bypasses both -- this is immutable to the ingest identity, which is '
    'the actor being audited, not to a DBA.';

CREATE INDEX IF NOT EXISTS ingest_events_source_idx
    ON ingest_events (source_uri, occurred_at DESC);

-- ── Privileges ───────────────────────────────────────────────────────────
-- Granted narrowly, then revoked explicitly. The REVOKE is not redundant: it
-- makes a later blanket `GRANT ... ON ALL TABLES` visible as a conflict rather
-- than silently reopening the audit log.
GRANT USAGE ON SCHEMA public TO rag_ingest_role;
GRANT INSERT, SELECT ON ingest_events TO rag_ingest_role;
REVOKE UPDATE, DELETE, TRUNCATE ON ingest_events FROM rag_ingest_role;

-- `rag_app_role` is the agent-facing query path and gets nothing here: audit
-- rows carry source URIs across every department and are not corpus content.
REVOKE ALL ON ingest_events FROM rag_app_role;

-- No sequence grant appears above on purpose. `bigserial` would need
-- `GRANT USAGE ON SEQUENCE`, and omitting it fails every INSERT at runtime;
-- an identity column is reachable through the column's INSERT privilege.

ALTER TABLE ingest_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ingest_events_append ON ingest_events;
CREATE POLICY ingest_events_append ON ingest_events
    FOR INSERT TO rag_ingest_role
    WITH CHECK (true);

DROP POLICY IF EXISTS ingest_events_read ON ingest_events;
CREATE POLICY ingest_events_read ON ingest_events
    FOR SELECT TO rag_ingest_role
    USING (true);

-- Deliberately absent: any FOR UPDATE, FOR DELETE or FOR ALL policy. With
-- FORCE ROW LEVEL SECURITY and no permissive policy, those commands are denied
-- even if a future migration re-grants the privilege by accident.
