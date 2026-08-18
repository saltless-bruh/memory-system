"""Integration tests for PostgreSQL Row-Level Security (RLS) policies.

Validates fail-closed kernel RLS enforcement for rag_documents and rag_chunks
under restricted application role `rag_app_role`.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio

from scout.ingest import get_pg_connection

TEST_AI_ENG_URI = "raw/ai_eng/test_rls_arch.md"
TEST_REDTEAM_URI = "raw/redteam/test_rls_exploit.md"


async def get_app_connection() -> asyncpg.Connection:
    """Creates a connection authenticated as the restricted rag_app_role."""
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    db = os.environ.get("POSTGRES_DB", "snp_rag")
    user = os.environ.get("POSTGRES_APP_USER", "rag_app_role")
    password = os.environ.get("POSTGRES_APP_PASSWORD", "rag_app_secret")

    return await asyncpg.connect(
        host=host,
        port=port,
        database=db,
        user=user,
        password=password,
    )


@pytest_asyncio.fixture(autouse=True)
async def seed_rls_test_data() -> AsyncGenerator[dict[str, uuid.UUID], None]:
    """Seeds test documents with department-restricted clearances as admin, then cleans up."""
    try:
        admin_conn = await get_pg_connection()
    except Exception as e:
        pytest.skip(f"PostgreSQL admin connection failed: {e}")

    ai_doc_id = uuid.uuid4()
    red_doc_id = uuid.uuid4()

    try:
        # Seed AI Engineering document and chunk
        await admin_conn.execute(
            """
            INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (source_uri) DO NOTHING;
            """,
            ai_doc_id,
            TEST_AI_ENG_URI,
            ["ai_eng"],
            "AI Engineering Specs",
        )
        await admin_conn.execute(
            """
            INSERT INTO rag_chunks (chunk_id, doc_id, chunk_index, chunk_text)
            VALUES ($1, $2, $3, $4);
            """,
            uuid.uuid4(),
            ai_doc_id,
            0,
            "Confidential AI engineering neural weights and training cluster architecture.",
        )

        # Seed Redteam document and chunk
        await admin_conn.execute(
            """
            INSERT INTO rag_documents (doc_id, source_uri, allowed_depts, title)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (source_uri) DO NOTHING;
            """,
            red_doc_id,
            TEST_REDTEAM_URI,
            ["redteam"],
            "Redteam Exploit Chain",
        )
        await admin_conn.execute(
            """
            INSERT INTO rag_chunks (chunk_id, doc_id, chunk_index, chunk_text)
            VALUES ($1, $2, $3, $4);
            """,
            uuid.uuid4(),
            red_doc_id,
            0,
            "Restricted redteam exploit payloads and domain admin lateral movement techniques.",
        )

        yield {"ai_doc_id": ai_doc_id, "red_doc_id": red_doc_id}

    finally:
        await admin_conn.execute(
            "DELETE FROM rag_documents WHERE source_uri IN ($1, $2);",
            TEST_AI_ENG_URI,
            TEST_REDTEAM_URI,
        )
        await admin_conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rls_fail_closed_without_session_clearance() -> None:
    """Queries as rag_app_role without SET LOCAL scout.current_depts must return 0 rows."""
    try:
        app_conn = await get_app_connection()
    except Exception as e:
        pytest.skip(f"PostgreSQL app connection failed: {e}")

    try:
        # 1. Query rag_documents directly
        docs = await app_conn.fetch(
            "SELECT * FROM rag_documents WHERE source_uri IN ($1, $2);",
            TEST_AI_ENG_URI,
            TEST_REDTEAM_URI,
        )
        assert len(docs) == 0, f"Expected 0 documents without clearance, got {len(docs)}"

        # 2. Query rag_chunks directly via join
        chunks = await app_conn.fetch(
            """
            SELECT c.* FROM rag_chunks c
            JOIN rag_documents d ON d.doc_id = c.doc_id
            WHERE d.source_uri IN ($1, $2);
            """,
            TEST_AI_ENG_URI,
            TEST_REDTEAM_URI,
        )
        assert len(chunks) == 0, f"Expected 0 chunks without clearance, got {len(chunks)}"

        # 3. Direct scan on rag_chunks
        direct_chunks = await app_conn.fetch(
            "SELECT * FROM rag_chunks WHERE chunk_text LIKE '%Confidential AI%' OR chunk_text LIKE '%Restricted redteam%';"
        )
        assert len(direct_chunks) == 0, f"Expected 0 direct chunks, got {len(direct_chunks)}"

    finally:
        await app_conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rls_department_isolation_ai_eng() -> None:
    """Setting scout.current_depts = 'ai_eng' returns only ai_eng docs/chunks and denies redteam."""
    try:
        app_conn = await get_app_connection()
    except Exception as e:
        pytest.skip(f"PostgreSQL app connection failed: {e}")

    try:
        async with app_conn.transaction():
            await app_conn.execute("SET LOCAL scout.current_depts = 'ai_eng';")

            # 1. Check rag_documents isolation
            docs = await app_conn.fetch(
                "SELECT * FROM rag_documents WHERE source_uri IN ($1, $2);",
                TEST_AI_ENG_URI,
                TEST_REDTEAM_URI,
            )
            assert len(docs) == 1
            assert docs[0]["source_uri"] == TEST_AI_ENG_URI
            assert "ai_eng" in docs[0]["allowed_depts"]

            # 2. Check rag_chunks isolation via join
            chunks = await app_conn.fetch(
                """
                SELECT c.*, d.source_uri FROM rag_chunks c
                JOIN rag_documents d ON d.doc_id = c.doc_id
                WHERE d.source_uri IN ($1, $2);
                """,
                TEST_AI_ENG_URI,
                TEST_REDTEAM_URI,
            )
            assert len(chunks) == 1
            assert chunks[0]["source_uri"] == TEST_AI_ENG_URI
            assert "Confidential AI engineering" in chunks[0]["chunk_text"]

            # 3. Direct scan on rag_chunks should only see ai_eng chunk
            direct_chunks = await app_conn.fetch(
                "SELECT * FROM rag_chunks WHERE chunk_text LIKE '%Confidential AI%' OR chunk_text LIKE '%Restricted redteam%';"
            )
            assert len(direct_chunks) == 1
            assert "Confidential AI engineering" in direct_chunks[0]["chunk_text"]

    finally:
        await app_conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rls_department_isolation_redteam() -> None:
    """Setting scout.current_depts = 'redteam' returns only redteam docs/chunks and denies ai_eng."""
    try:
        app_conn = await get_app_connection()
    except Exception as e:
        pytest.skip(f"PostgreSQL app connection failed: {e}")

    try:
        async with app_conn.transaction():
            await app_conn.execute("SET LOCAL scout.current_depts = 'redteam';")

            # 1. Check rag_documents isolation
            docs = await app_conn.fetch(
                "SELECT * FROM rag_documents WHERE source_uri IN ($1, $2);",
                TEST_AI_ENG_URI,
                TEST_REDTEAM_URI,
            )
            assert len(docs) == 1
            assert docs[0]["source_uri"] == TEST_REDTEAM_URI
            assert "redteam" in docs[0]["allowed_depts"]

            # 2. Check rag_chunks isolation
            chunks = await app_conn.fetch(
                """
                SELECT c.*, d.source_uri FROM rag_chunks c
                JOIN rag_documents d ON d.doc_id = c.doc_id
                WHERE d.source_uri IN ($1, $2);
                """,
                TEST_AI_ENG_URI,
                TEST_REDTEAM_URI,
            )
            assert len(chunks) == 1
            assert chunks[0]["source_uri"] == TEST_REDTEAM_URI
            assert "Restricted redteam exploit" in chunks[0]["chunk_text"]

    finally:
        await app_conn.close()
