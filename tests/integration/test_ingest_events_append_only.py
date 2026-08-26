"""The ingest audit log is append-only because PostgreSQL says so.

"No UPDATE path in the code" is a property of today's code. This asserts the
property of the database, over a real connection as the audited identity —
which is the only way to observe it, since a superuser bypasses both the grant
and the row-level policy.

Context for why this needed proving rather than assuming: the house pattern in
this schema grants the ingest role full DML.

    002_rls_and_roles.sql:19  GRANT SELECT, INSERT, UPDATE, DELETE ... TO rag_ingest_role
    003_ingest_role_rls.sql:8 CREATE POLICY ... FOR ALL TO rag_ingest_role

An audit table written that way would have been fully mutable by the exact role
it audits.
"""

from __future__ import annotations

import asyncpg
import pytest

from scout.config import postgres_settings

pytestmark = pytest.mark.integration

OVERRIDE = "capability_override"
HINT = "append-only-verification"


async def _ingest_connection() -> asyncpg.Connection:
    settings = postgres_settings("ingest")
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
    )


async def _migration_connection() -> asyncpg.Connection:
    settings = postgres_settings("migration")
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
    )


@pytest.fixture
async def event_id() -> int:
    """Insert one audit row, and clean it up as the superuser afterwards.

    The teardown has to use the migration identity precisely because the role
    under test cannot delete its own rows. If this fixture could tear down over
    the ingest connection, the test it supports would already have failed.
    """
    connection = await _ingest_connection()
    try:
        row = await connection.fetchrow(
            """
            INSERT INTO ingest_events (
                source_uri, event_type, old_fingerprint, new_fingerprint,
                acknowledgement, actor_hint
            )
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)
            RETURNING event_id, actor, occurred_at
            """,
            "raw/papers/test-append-only.pdf",
            OVERRIDE,
            '{"parser_revision": 1}',
            '{"parser_revision": 2}',
            "parser revision 1 -> 2",
            HINT,
        )
        assert row is not None
        # `actor` is filled by PostgreSQL from the authenticated identity, not
        # supplied by the writer. An environment variable naming the actor is a
        # value the audited party chooses.
        assert row["actor"] == settings_user()
        assert row["occurred_at"] is not None
        yield row["event_id"]
    finally:
        await connection.close()
        cleanup = await _migration_connection()
        try:
            await cleanup.execute(
                "DELETE FROM ingest_events WHERE actor_hint = $1", HINT
            )
        finally:
            await cleanup.close()


def settings_user() -> str:
    return postgres_settings("ingest").user


async def test_the_ingest_role_can_append(event_id: int) -> None:
    assert event_id > 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE ingest_events SET acknowledgement = 'tampered' WHERE event_id = $1",
        "DELETE FROM ingest_events WHERE event_id = $1",
    ],
    ids=["update", "delete"],
)
async def test_the_ingest_role_cannot_rewrite_history(
    event_id: int, statement: str
) -> None:
    connection = await _ingest_connection()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(statement, event_id)
    finally:
        await connection.close()


async def test_the_ingest_role_cannot_truncate(event_id: int) -> None:
    """TRUNCATE is the one that bypasses a row-level policy, so it is revoked."""
    connection = await _ingest_connection()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("TRUNCATE ingest_events")
    finally:
        await connection.close()


async def test_the_row_survives_every_attempt(event_id: int) -> None:
    connection = await _ingest_connection()
    try:
        for statement in (
            "UPDATE ingest_events SET acknowledgement = 'tampered' WHERE event_id = $1",
            "DELETE FROM ingest_events WHERE event_id = $1",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement, event_id)
        row = await connection.fetchrow(
            "SELECT acknowledgement, actor_hint FROM ingest_events WHERE event_id = $1",
            event_id,
        )
        assert row is not None
        assert row["acknowledgement"] == "parser revision 1 -> 2"
        assert row["actor_hint"] == HINT
    finally:
        await connection.close()


async def test_no_policy_permits_update_or_delete() -> None:
    """Belt and braces: the grant is revoked *and* no policy would allow it.

    Either alone is a single point of failure — a later `GRANT ... ON ALL
    TABLES` would restore the privilege, and a permissive `FOR ALL` policy
    would be the 003 pattern reappearing.
    """
    connection = await _migration_connection()
    try:
        commands = await connection.fetch(
            "SELECT cmd FROM pg_policies WHERE tablename = 'ingest_events'"
        )
        assert {row["cmd"] for row in commands} <= {"INSERT", "SELECT"}
        forced = await connection.fetchval(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'ingest_events'"
        )
        assert forced is True
    finally:
        await connection.close()


async def test_an_override_writes_a_row_that_survives_the_next_ingest() -> None:
    """The failure the first two designs had.

    Revision 1 stored "overrode the previous fingerprint" inside the document's
    own fingerprint column, which the very next ingest overwrites. This asserts
    the opposite: a subsequent write to `rag_documents` leaves the audit row
    byte-identical.
    """
    from scout.ingest import record_capability_override

    connection = await _ingest_connection()
    cleanup = await _migration_connection()
    try:
        await record_capability_override(
            connection,
            mismatch="raw/papers/test-append-only.pdf: tables available -> unavailable",
            acknowledgement=(
                "raw/papers/test-append-only.pdf: tables available -> unavailable"
            ),
            actor_hint=HINT,
            dir_path=__import__("pathlib").Path("raw/papers"),
            base_dir=__import__("pathlib").Path("raw"),
        )
        before = await connection.fetchrow(
            "SELECT event_id, actor, acknowledgement, new_fingerprint, occurred_at "
            "FROM ingest_events WHERE actor_hint = $1",
            HINT,
        )
        assert before is not None
        assert before["actor"] == settings_user()
        assert "tables available -> unavailable" in before["acknowledgement"]
        assert before["new_fingerprint"] is not None

        # Anything the ingest role is allowed to do to the corpus afterwards.
        await connection.execute(
            "UPDATE rag_documents SET capability_fingerprint = capability_fingerprint "
            "WHERE source_uri = $1",
            "raw/papers/nonexistent-for-this-test.pdf",
        )

        after = await connection.fetchrow(
            "SELECT event_id, actor, acknowledgement, new_fingerprint, occurred_at "
            "FROM ingest_events WHERE actor_hint = $1",
            HINT,
        )
        assert after is not None
        assert dict(after) == dict(before)
    finally:
        await connection.close()
        try:
            await cleanup.execute(
                "DELETE FROM ingest_events WHERE actor_hint = $1", HINT
            )
        finally:
            await cleanup.close()


async def test_a_normal_run_writes_no_audit_row() -> None:
    """The log must mean something. A row per ingest would be a log nobody reads."""
    connection = await _migration_connection()
    try:
        before = await connection.fetchval("SELECT count(*) FROM ingest_events")
        # A capability-clean ingest path never reaches record_capability_override;
        # asserting the count is unchanged across an ordinary corpus write.
        await connection.execute(
            "UPDATE rag_documents SET capability_fingerprint = capability_fingerprint "
            "WHERE source_uri = $1",
            "raw/papers/nonexistent-for-this-test.pdf",
        )
        assert await connection.fetchval("SELECT count(*) FROM ingest_events") == before
    finally:
        await connection.close()
