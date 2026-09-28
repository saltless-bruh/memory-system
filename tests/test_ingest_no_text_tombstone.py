"""A source that yields no text still leaves a record of why.

`ingest_document` withdraws a no-text source's rows on purpose (audit finding
B3: fabricated image descriptions survived a re-ingest). But it returned before
`extraction_state` was ever computed, so no row and no `extraction_status` was
written, and `snpmemory verify-extraction` -- which reads only
`rag_documents.extraction_status` -- could not name the document at all. An
image whose vision transcription failed was indistinguishable, after the fact,
from a file that never existed.

OWNER RULING (audit 2026-09-26): persist an extraction_status tombstone, and
carry `vlm_status` in `extraction_state`, so verify-extraction can name them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from scout.ingest import ingest_document
from scout.parsers import extraction_state
from tests.fakes import FakeEmbedder


def test_a_failed_vision_transcription_is_an_incomplete_extraction() -> None:
    """An unconfigured VLM read as `complete: True` -- the parse lost everything
    the image carries and the record said nothing was lost."""
    for status in ("unconfigured", "unavailable"):
        record = extraction_state({"type": "image", "vlm_status": status})
        assert record["complete"] is False
        assert record["incomplete"] == ["vision"]
        assert record["extractors"] == {"vision": status}


def test_a_transcribed_image_is_complete() -> None:
    """The control: the guard must not call every image degraded."""
    assert extraction_state({"type": "image", "vlm_status": "ok"}) == {
        "extractors": {"vision": "ok"},
        "complete": True,
    }


class _Embedder(FakeEmbedder):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("a source with no text has nothing to embed")


class _Connection:
    """Records the document write; says how many chunks an earlier run left."""

    def __init__(self, prior_chunks: int = 0) -> None:
        self.prior_chunks = prior_chunks
        self.documents: list[tuple[str, tuple[object, ...]]] = []
        self.statements: list[str] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchrow(self, query: str, *args: object) -> dict[str, int]:
        self.documents.append((query, args))
        return {"doc_id": 1}

    async def execute(self, query: str, *_args: object) -> str:
        self.statements.append(query)
        if query.lstrip().startswith("DELETE FROM rag_chunks"):
            return f"DELETE {self.prior_chunks}"
        return "OK"

    async def close(self) -> None:
        return None


def _recorded(conn: _Connection) -> dict[str, Any]:
    (query, args), *_ = conn.documents
    assert "INSERT INTO rag_documents" in query
    assert "extraction_status" in query
    return dict(json.loads(str(args[4])))


@pytest.mark.asyncio
async def test_an_image_the_vlm_could_not_read_leaves_a_named_tombstone(
    tmp_path: Path,
) -> None:
    image = tmp_path / "raw" / "diagram.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    conn = _Connection()

    result = await ingest_document(
        image,
        ["infra"],
        conn=conn,
        embedder=_Embedder(),
        base_dir=tmp_path,
        env={},  # no vision route: the parser records `unconfigured`
    )

    recorded = _recorded(conn)
    assert recorded["complete"] is False
    assert recorded["extractors"] == {"vision": "unconfigured"}
    assert "vision" in recorded["incomplete"]
    assert "text" in recorded["incomplete"]
    assert recorded["no_text"] is True
    # The tombstone carries no evidence: whatever chunks the address held are
    # withdrawn, and none are written.
    assert any(s.lstrip().startswith("DELETE FROM rag_chunks") for s in conn.statements)
    assert not any("INSERT INTO rag_chunks" in s for s in conn.statements)
    assert result["chunks_count"] == 0
    assert result["status"] == "skipped_empty"
    assert result["extraction_status"] == recorded


@pytest.mark.asyncio
async def test_a_source_that_had_evidence_reports_it_purged(tmp_path: Path) -> None:
    image = tmp_path / "raw" / "diagram.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    conn = _Connection(prior_chunks=3)

    result = await ingest_document(
        image, ["infra"], conn=conn, embedder=_Embedder(), base_dir=tmp_path, env={}
    )

    assert result["status"] == "purged_empty"
    assert _recorded(conn)["no_text"] is True


@pytest.mark.asyncio
async def test_a_genuinely_empty_file_is_told_apart_from_a_failed_extractor(
    tmp_path: Path,
) -> None:
    """Both are named, and the record says which one it was."""
    empty = tmp_path / "raw" / "blank.md"
    empty.parent.mkdir()
    empty.write_text("", encoding="utf-8")
    conn = _Connection()

    await ingest_document(
        empty, ["infra"], conn=conn, embedder=_Embedder(), base_dir=tmp_path, env={}
    )

    recorded = _recorded(conn)
    assert recorded["extractors"] == {}
    assert recorded["incomplete"] == ["text"]
    assert recorded["complete"] is False


@pytest.mark.asyncio
async def test_a_dry_run_writes_no_tombstone(tmp_path: Path) -> None:
    empty = tmp_path / "raw" / "blank.md"
    empty.parent.mkdir()
    empty.write_text("", encoding="utf-8")

    result = await ingest_document(
        empty, ["infra"], embedder=_Embedder(), base_dir=tmp_path, dry_run=True
    )

    assert result["status"] == "dry_run_ok"
    assert result["chunks_count"] == 0
