"""tests/integration/test_postgres_rls.py — Integration tests for PostgreSQL Fail-Closed RLS.

Tests kernel-enforced department isolation and public document visibility
using the non-superuser `rag_app_role`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from scout.cli.commands.verify import verify_extraction
from scout.cli.config import Config
from scout.cli.registry import Prerequisite
from scout.cli.result import ExitCode
from scout.config import postgres_settings
from scout.ingest import DocumentAclMap, reconcile_deletions

pytestmark = pytest.mark.integration


async def get_app_connection() -> asyncpg.Connection:
    settings = postgres_settings("query")
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
    )


async def get_ingest_connection() -> asyncpg.Connection:
    settings = postgres_settings("ingest")
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
    )


async def get_master_connection() -> asyncpg.Connection:
    settings = postgres_settings("migration")
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
    )


@pytest.fixture
async def setup_test_documents():
    """Seed test documents with different department clearance tags."""
    master = await get_master_connection()
    doc_ai = uuid.uuid4()
    doc_red = uuid.uuid4()
    doc_pub = uuid.uuid4()

    try:
        # Seed test documents
        await master.execute(
            "INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title) VALUES ($1, $2, $3, $4)",
            doc_ai,
            f"raw/ai_{doc_ai}.md",
            ["ai_eng"],
            "AI Doc",
        )
        await master.execute(
            "INSERT INTO rag_chunks (doc_id, chunk_index, chunk_text) VALUES ($1, 0, 'AI confidential text')",
            doc_ai,
        )

        await master.execute(
            "INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title) VALUES ($1, $2, $3, $4)",
            doc_red,
            f"raw/red_{doc_red}.md",
            ["redteam"],
            "Redteam Doc",
        )
        await master.execute(
            "INSERT INTO rag_chunks (doc_id, chunk_index, chunk_text) VALUES ($1, 0, 'Redteam exploit text')",
            doc_red,
        )

        await master.execute(
            "INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title) VALUES ($1, $2, $3, $4)",
            doc_pub,
            f"raw/pub_{doc_pub}.md",
            ["all"],
            "Public Doc",
        )
        await master.execute(
            "INSERT INTO rag_chunks (doc_id, chunk_index, chunk_text) VALUES ($1, 0, 'Public shared knowledge')",
            doc_pub,
        )

        yield {"ai": doc_ai, "red": doc_red, "pub": doc_pub}

    finally:
        await master.execute(
            "DELETE FROM rag_documents WHERE doc_id IN ($1, $2, $3)",
            doc_ai,
            doc_red,
            doc_pub,
        )
        await master.close()


@pytest.mark.asyncio
async def test_unauthenticated_app_role_fails_closed(setup_test_documents):
    """When scout.current_depts is not set, rag_app_role sees 0 documents and 0 chunks."""
    doc_ids = setup_test_documents
    app_conn = await get_app_connection()
    try:
        # No session clearance set
        docs = await app_conn.fetch(
            "SELECT doc_id FROM rag_documents WHERE doc_id IN ($1, $2, $3)",
            doc_ids["ai"],
            doc_ids["red"],
            doc_ids["pub"],
        )
        assert len(docs) == 0, (
            "Unauthenticated queries must fail closed (0 documents returned)"
        )

        chunks = await app_conn.fetch(
            "SELECT chunk_id FROM rag_chunks WHERE doc_id IN ($1, $2, $3)",
            doc_ids["ai"],
            doc_ids["red"],
            doc_ids["pub"],
        )
        assert len(chunks) == 0, (
            "Unauthenticated queries must fail closed (0 chunks returned)"
        )
    finally:
        await app_conn.close()


@pytest.mark.asyncio
async def test_authenticated_ai_eng_clearance_isolation(setup_test_documents):
    """When scout.current_depts = 'ai_eng', caller sees AI + Public docs, but 0 Redteam docs."""
    doc_ids = setup_test_documents
    app_conn = await get_app_connection()
    try:
        async with app_conn.transaction():
            await app_conn.execute("SET LOCAL scout.current_depts = 'ai_eng'")

            docs = await app_conn.fetch(
                "SELECT doc_id FROM rag_documents WHERE doc_id IN ($1, $2, $3)",
                doc_ids["ai"],
                doc_ids["red"],
                doc_ids["pub"],
            )
            returned_doc_ids = {r["doc_id"] for r in docs}
            assert doc_ids["ai"] in returned_doc_ids, "ai_eng document must be visible"
            assert doc_ids["pub"] in returned_doc_ids, (
                "public 'all' document must be visible to authenticated caller"
            )
            assert doc_ids["red"] not in returned_doc_ids, (
                "redteam document MUST be denied to ai_eng"
            )

            chunks = await app_conn.fetch(
                "SELECT doc_id, chunk_text FROM rag_chunks WHERE doc_id IN ($1, $2, $3)",
                doc_ids["ai"],
                doc_ids["red"],
                doc_ids["pub"],
            )
            returned_chunk_docs = {r["doc_id"] for r in chunks}
            assert doc_ids["ai"] in returned_chunk_docs
            assert doc_ids["pub"] in returned_chunk_docs
            assert doc_ids["red"] not in returned_chunk_docs
    finally:
        await app_conn.close()


@pytest.mark.asyncio
async def test_authenticated_redteam_clearance_isolation(setup_test_documents):
    """When scout.current_depts = 'redteam', caller sees Redteam + Public docs, but 0 AI docs."""
    doc_ids = setup_test_documents
    app_conn = await get_app_connection()
    try:
        async with app_conn.transaction():
            await app_conn.execute("SET LOCAL scout.current_depts = 'redteam'")

            docs = await app_conn.fetch(
                "SELECT doc_id FROM rag_documents WHERE doc_id IN ($1, $2, $3)",
                doc_ids["ai"],
                doc_ids["red"],
                doc_ids["pub"],
            )
            returned_doc_ids = {r["doc_id"] for r in docs}
            assert doc_ids["red"] in returned_doc_ids, (
                "redteam document must be visible"
            )
            assert doc_ids["pub"] in returned_doc_ids, (
                "public 'all' document must be visible"
            )
            assert doc_ids["ai"] not in returned_doc_ids, (
                "ai_eng document MUST be denied to redteam"
            )

            chunks = await app_conn.fetch(
                "SELECT doc_id FROM rag_chunks WHERE doc_id IN ($1, $2, $3)",
                doc_ids["ai"],
                doc_ids["red"],
                doc_ids["pub"],
            )
            returned_chunk_docs = {r["doc_id"] for r in chunks}
            assert doc_ids["red"] in returned_chunk_docs
            assert doc_ids["pub"] in returned_chunk_docs
            assert doc_ids["ai"] not in returned_chunk_docs
    finally:
        await app_conn.close()


@pytest.mark.asyncio
async def test_ingest_role_has_crud_through_forced_rls() -> None:
    conn = await get_ingest_connection()
    doc_id = uuid.uuid4()
    try:
        await conn.execute(
            "INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title) "
            "VALUES ($1, $2, $3, $4)",
            doc_id,
            f"raw/ingest_{doc_id}.md",
            ["infra"],
            "Ingest role test",
        )
        assert (
            await conn.fetchval(
                "SELECT title FROM rag_documents WHERE doc_id = $1", doc_id
            )
            == "Ingest role test"
        )
        await conn.execute(
            "UPDATE rag_documents SET title = $2 WHERE doc_id = $1",
            doc_id,
            "Updated",
        )
        assert (
            await conn.fetchval(
                "SELECT title FROM rag_documents WHERE doc_id = $1", doc_id
            )
            == "Updated"
        )
        await conn.execute("DELETE FROM rag_documents WHERE doc_id = $1", doc_id)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM rag_documents WHERE doc_id = $1", doc_id
            )
            == 0
        )
    finally:
        await conn.execute("DELETE FROM rag_documents WHERE doc_id = $1", doc_id)
        await conn.close()


@pytest.mark.asyncio
async def test_runtime_roles_are_not_privileged_or_owners() -> None:
    conn = await get_master_connection()
    try:
        rows = await conn.fetch(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = ANY($1::text[])",
            ["rag_app_role", "rag_ingest_role"],
        )
        assert {row["rolname"] for row in rows} == {"rag_app_role", "rag_ingest_role"}
        assert all(not row["rolsuper"] and not row["rolbypassrls"] for row in rows)
        owners = await conn.fetchval(
            "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
            "WHERE c.relname = ANY($1::text[]) "
            "AND r.rolname = ANY($2::text[])",
            ["rag_documents", "rag_chunks"],
            ["rag_app_role", "rag_ingest_role"],
        )
        assert owners == 0
    finally:
        await conn.close()


#: Every wiki page is written with all four departments (scout/wiki_ingest.py,
#: WIKI_ALLOWED_DEPARTMENTS). The rows above carry one department or `all`, so
#: nothing here showed that narrowing to one department still sees the wiki.
WIKI_DEPARTMENTS = ["redteam", "blueteam", "ai_eng", "infra"]


@pytest.mark.asyncio
async def test_a_wiki_row_is_visible_under_every_single_department_narrowing() -> None:
    """Narrowing is a no-op on the wiki tier, and it still fails closed unscoped."""
    master = await get_master_connection()
    doc_id = uuid.uuid4()
    try:
        await master.execute(
            "INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title) "
            "VALUES ($1, $2, $3, $4)",
            doc_id,
            f"wiki/rls_{doc_id}.md",
            WIKI_DEPARTMENTS,
            "Wiki RLS page",
        )
        await master.execute(
            "INSERT INTO rag_chunks (doc_id, chunk_index, chunk_text, metadata) "
            "VALUES ($1, 0, 'wiki tier text', $2::jsonb)",
            doc_id,
            '{"corpus": "wiki"}',
        )

        app_conn = await get_app_connection()
        try:
            unscoped = await app_conn.fetchval(
                "SELECT count(*) FROM rag_chunks WHERE doc_id = $1", doc_id
            )
            assert unscoped == 0, "an unscoped caller must see no wiki chunk"

            for department in WIKI_DEPARTMENTS:
                async with app_conn.transaction():
                    await app_conn.execute(
                        "SELECT set_config('scout.current_depts', $1, true)",
                        department,
                    )
                    docs = await app_conn.fetchval(
                        "SELECT count(*) FROM rag_documents WHERE doc_id = $1",
                        doc_id,
                    )
                    chunks = await app_conn.fetchval(
                        "SELECT count(*) FROM rag_chunks WHERE doc_id = $1", doc_id
                    )
                assert (docs, chunks) == (1, 1), (
                    f"a caller narrowed to {department!r} lost the wiki page"
                )
        finally:
            await app_conn.close()
    finally:
        await master.execute("DELETE FROM rag_documents WHERE doc_id = $1", doc_id)
        await master.close()


@pytest.mark.asyncio
async def test_reconcile_purges_a_row_whose_file_no_acl_rule_grants(
    tmp_path: Path,
) -> None:
    """Revoking a rule revokes access: the unmapped branch of reconcile_deletions.

    The file is still on disk, so only the ACL map can condemn it. Every row is
    under a directory name unique to this run, and reconcile acts only on rows
    under the directory it is given, so no other row in the database is at risk.
    """
    root = tmp_path / f"rlsraw_{uuid.uuid4().hex}"
    (root / "kept").mkdir(parents=True)
    (root / "revoked").mkdir()
    (root / "kept" / "a.md").write_text("# kept\n", encoding="utf-8")
    (root / "revoked" / "b.md").write_text("# revoked\n", encoding="utf-8")
    acl_file = root / ".acl.yaml"
    acl_file.write_text(
        'version: 1\nrules:\n  - path: "kept/**"\n    departments: [infra]\n',
        encoding="utf-8",
    )
    acl = DocumentAclMap.from_file(acl_file)
    kept_uri = f"{root.name}/kept/a.md"
    revoked_uri = f"{root.name}/revoked/b.md"

    conn = await get_ingest_connection()
    ids = {kept_uri: uuid.uuid4(), revoked_uri: uuid.uuid4()}
    try:
        for uri, doc_id in ids.items():
            await conn.execute(
                "INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title) "
                "VALUES ($1, $2, $3, $4)",
                doc_id,
                uri,
                ["infra"],
                uri,
            )
            await conn.execute(
                "INSERT INTO rag_chunks (doc_id, chunk_index, chunk_text) "
                "VALUES ($1, 0, 'untiered raw text')",
                doc_id,
            )

        deleted = await reconcile_deletions(root, conn=conn, acl=acl)

        assert deleted == [revoked_uri]
        remaining = {
            row["source_uri"]
            for row in await conn.fetch(
                "SELECT source_uri FROM rag_documents WHERE doc_id = ANY($1::uuid[])",
                list(ids.values()),
            )
        }
        assert remaining == {kept_uri}
    finally:
        await conn.execute(
            "DELETE FROM rag_documents WHERE doc_id = ANY($1::uuid[])",
            list(ids.values()),
        )
        await conn.close()


def test_verify_extraction_reads_single_department_rows_as_the_query_role() -> None:
    """The scoped SELECT behind `snpmemory verify-extraction`, under real RLS.

    The command runs as the `query` role, which sees nothing until
    `scout.current_depts` is set. Its offline tests stub asyncpg, so none showed
    that the clearance it sets reaches a row granted to one department.
    Synchronous because the command itself calls `asyncio.run`.
    """
    doc_id = uuid.uuid4()
    uri = f"raw/extraction_{doc_id}.pdf"

    async def seed() -> None:
        master = await get_master_connection()
        try:
            await master.execute(
                "INSERT INTO rag_documents "
                "(doc_id, source_uri, allowed_depts, title, extraction_status) "
                "VALUES ($1, $2, $3, $4, $5::jsonb)",
                doc_id,
                uri,
                ["redteam"],
                "Extraction RLS probe",
                '{"complete": false, "incomplete": ["figures"]}',
            )
        finally:
            await master.close()

    async def remove() -> None:
        master = await get_master_connection()
        try:
            await master.execute("DELETE FROM rag_documents WHERE doc_id = $1", doc_id)
        finally:
            await master.close()

    asyncio.run(seed())
    try:
        result = verify_extraction(
            config=Config(prerequisite=Prerequisite.LOCAL, values=dict(os.environ))
        )
    finally:
        asyncio.run(remove())

    named = {document["source_uri"] for document in result.data["documents"]}
    assert uri in named, "a redteam-only row was invisible to verify-extraction"
    assert result.exit_code is ExitCode.SEMANTIC_FAILURE
