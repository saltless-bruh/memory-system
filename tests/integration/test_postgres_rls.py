"""tests/integration/test_postgres_rls.py — Integration tests for PostgreSQL Fail-Closed RLS.

Tests kernel-enforced department isolation and public document visibility
using the non-superuser `rag_app_role`.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from scout.config import postgres_settings

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
            doc_ai, f"raw/ai_{doc_ai}.md", ["ai_eng"], "AI Doc"
        )
        await master.execute(
            "INSERT INTO rag_chunks (doc_id, chunk_index, chunk_text) VALUES ($1, 0, 'AI confidential text')",
            doc_ai
        )

        await master.execute(
            "INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title) VALUES ($1, $2, $3, $4)",
            doc_red, f"raw/red_{doc_red}.md", ["redteam"], "Redteam Doc"
        )
        await master.execute(
            "INSERT INTO rag_chunks (doc_id, chunk_index, chunk_text) VALUES ($1, 0, 'Redteam exploit text')",
            doc_red
        )

        await master.execute(
            "INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title) VALUES ($1, $2, $3, $4)",
            doc_pub, f"raw/pub_{doc_pub}.md", ["all"], "Public Doc"
        )
        await master.execute(
            "INSERT INTO rag_chunks (doc_id, chunk_index, chunk_text) VALUES ($1, 0, 'Public shared knowledge')",
            doc_pub
        )

        yield {"ai": doc_ai, "red": doc_red, "pub": doc_pub}

    finally:
        await master.execute("DELETE FROM rag_documents WHERE doc_id IN ($1, $2, $3)", doc_ai, doc_red, doc_pub)
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
            doc_ids["ai"], doc_ids["red"], doc_ids["pub"]
        )
        assert len(docs) == 0, "Unauthenticated queries must fail closed (0 documents returned)"

        chunks = await app_conn.fetch(
            "SELECT chunk_id FROM rag_chunks WHERE doc_id IN ($1, $2, $3)",
            doc_ids["ai"], doc_ids["red"], doc_ids["pub"]
        )
        assert len(chunks) == 0, "Unauthenticated queries must fail closed (0 chunks returned)"
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
                doc_ids["ai"], doc_ids["red"], doc_ids["pub"]
            )
            returned_doc_ids = {r["doc_id"] for r in docs}
            assert doc_ids["ai"] in returned_doc_ids, "ai_eng document must be visible"
            assert doc_ids["pub"] in returned_doc_ids, "public 'all' document must be visible to authenticated caller"
            assert doc_ids["red"] not in returned_doc_ids, "redteam document MUST be denied to ai_eng"

            chunks = await app_conn.fetch(
                "SELECT doc_id, chunk_text FROM rag_chunks WHERE doc_id IN ($1, $2, $3)",
                doc_ids["ai"], doc_ids["red"], doc_ids["pub"]
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
                doc_ids["ai"], doc_ids["red"], doc_ids["pub"]
            )
            returned_doc_ids = {r["doc_id"] for r in docs}
            assert doc_ids["red"] in returned_doc_ids, "redteam document must be visible"
            assert doc_ids["pub"] in returned_doc_ids, "public 'all' document must be visible"
            assert doc_ids["ai"] not in returned_doc_ids, "ai_eng document MUST be denied to redteam"

            chunks = await app_conn.fetch(
                "SELECT doc_id FROM rag_chunks WHERE doc_id IN ($1, $2, $3)",
                doc_ids["ai"], doc_ids["red"], doc_ids["pub"]
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
        assert await conn.fetchval(
            "SELECT title FROM rag_documents WHERE doc_id = $1", doc_id
        ) == "Ingest role test"
        await conn.execute(
            "UPDATE rag_documents SET title = $2 WHERE doc_id = $1",
            doc_id,
            "Updated",
        )
        assert await conn.fetchval(
            "SELECT title FROM rag_documents WHERE doc_id = $1", doc_id
        ) == "Updated"
        await conn.execute("DELETE FROM rag_documents WHERE doc_id = $1", doc_id)
        assert await conn.fetchval(
            "SELECT count(*) FROM rag_documents WHERE doc_id = $1", doc_id
        ) == 0
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
