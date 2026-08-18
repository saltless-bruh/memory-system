"""Tests for V2 Multi-Modal Ingestion Pipeline (scripts/ingest_v2.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from scout.chunker import ContextualChunker, LiteLLMBatchEmbedder
from scout.parsers import parse_csv, parse_markdown
from scripts.ingest_v2 import get_pg_connection, ingest_document


def test_parse_markdown() -> None:
    content = """---
title: Test Protocol Spec
department: networking
---
# Introduction
This is the intro section.

## Details
This is the technical details section.
"""
    doc = parse_markdown(content, "raw/rfcs/test.md")
    assert doc.title == "Test Protocol Spec"
    assert len(doc.sections) >= 2
    assert "intro section" in doc.sections[0].text
    assert "technical details" in doc.sections[1].text


def test_parse_csv() -> None:
    content = """name,score,status
kerberoasting,0.95,active
tls-handshake,0.98,verified
"""
    doc = parse_csv(content, "raw/data/test.csv")
    assert doc.title == "Test"
    assert len(doc.sections) == 1
    assert "name: kerberoasting" in doc.sections[0].text
    assert "score: 0.95" in doc.sections[0].text


def test_contextual_chunker() -> None:
    content = "# Section 1\n" + ("TCP protocol details and sliding window flow control. " * 30)
    doc = parse_markdown(content, "raw/rfcs/tcp.md")
    chunker = ContextualChunker(max_chunk_chars=300)
    chunks = chunker.chunk_document(doc)

    assert len(chunks) > 1
    for chunk in chunks:
        assert "[Document: Tcp | Source: raw/rfcs/tcp.md" in chunk.context_prefix
        assert chunk.contextual_text.startswith("[Document:")


def test_batch_embedder_dimension() -> None:
    embedder = LiteLLMBatchEmbedder(dim=1024, allow_mock=True)
    res = embedder.embed_texts(["hello world", "protocol specification"])
    assert len(res) == 2
    assert len(res[0]) == 1024
    assert len(res[1]) == 1024


@pytest.mark.asyncio
async def test_ingest_document_to_postgres() -> None:
    """Integration test verifying end-to-end ingestion and idempotency in Postgres."""
    # Test Postgres connectivity
    try:
        conn = await get_pg_connection()
    except Exception as e:
        pytest.skip(f"Postgres container not reachable: {e}")

    embedder = LiteLLMBatchEmbedder(allow_mock=True)

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("# Ingestion Test\n\nThis is a live integration test for Postgres V2 ingestion.")
        temp_path = Path(f.name)

    try:
        # First Ingestion
        res1 = await ingest_document(
            file_path=temp_path,
            allowed_depts=["engineering", "all"],
            conn=conn,
            base_dir=temp_path.parent,
            embedder=embedder,
        )
        assert res1["status"] == "ingested_ok"
        assert res1["chunks_count"] >= 1

        # Verify DB records
        doc_row = await conn.fetchrow(
            "SELECT * FROM rag_documents WHERE source_uri = $1;",
            res1["source_uri"],
        )
        assert doc_row is not None
        assert doc_row["allowed_depts"] == ["engineering", "all"]

        chunk_rows = await conn.fetch(
            "SELECT * FROM rag_chunks WHERE doc_id = $1;",
            doc_row["doc_id"],
        )
        assert len(chunk_rows) == res1["chunks_count"]
        # Verify tsvector GIN column was automatically computed
        assert chunk_rows[0]["tsv"] is not None

        # Second Ingestion (Testing Idempotency)
        res2 = await ingest_document(
            file_path=temp_path,
            allowed_depts=["engineering", "security"],
            conn=conn,
            base_dir=temp_path.parent,
            embedder=embedder,
        )
        assert res2["status"] == "ingested_ok"

        # Verify no duplicate documents created
        doc_count = await conn.fetchval(
            "SELECT count(*) FROM rag_documents WHERE source_uri = $1;",
            res1["source_uri"],
        )
        assert doc_count == 1

        # Clean up test document
        await conn.execute(
            "DELETE FROM rag_documents WHERE doc_id = $1;", doc_row["doc_id"]
        )
    finally:
        await conn.close()
        temp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_reconcile_deletions() -> None:
    """Integration test verifying deletion reconciliation purges missing files from Postgres."""
    try:
        conn = await get_pg_connection()
    except Exception as e:
        pytest.skip(f"Postgres container not reachable: {e}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        dir_path = Path(tmp_dir) / "test_sandbox_raw"
        dir_path.mkdir()
        file1 = dir_path / "doc1.md"
        file1.write_text("# Doc 1\nContent of doc 1.")

        embedder = LiteLLMBatchEmbedder(allow_mock=True)

        try:
            # 1. Ingest directory
            from scout.ingest import ingest_directory, reconcile_deletions

            results = await ingest_directory(
                dir_path=dir_path,
                allowed_depts=["all"],
                dry_run=False,
                reconcile=False,
                embedder=embedder,
            )
            assert len(results) >= 1
            doc_uri = results[0]["source_uri"]

            # Confirm in DB
            exists = await conn.fetchval(
                "SELECT count(*) FROM rag_documents WHERE source_uri = $1;", doc_uri
            )
            assert exists == 1

            # 2. Delete file from disk
            file1.unlink()

            # 3. Run reconcile_deletions
            deleted_uris = await reconcile_deletions(dir_path=dir_path, conn=conn)
            assert doc_uri in deleted_uris

            # Confirm purged from DB
            exists_after = await conn.fetchval(
                "SELECT count(*) FROM rag_documents WHERE source_uri = $1;", doc_uri
            )
            assert exists_after == 0
        finally:
            await conn.close()



def test_image_parsing() -> None:
    from scout.parsers import parse_file

    with tempfile.NamedTemporaryFile(suffix=".png", mode="wb", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...")
        img_path = Path(f.name)

    try:
        doc = parse_file(img_path)
        assert doc.metadata.get("type") == "image"
        assert len(doc.sections) == 1
        assert "Visual Image Asset" in doc.sections[0].text
        assert "PNG" in doc.sections[0].text
    finally:
        img_path.unlink(missing_ok=True)

