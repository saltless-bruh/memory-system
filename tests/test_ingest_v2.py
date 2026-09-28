"""Tests for SNP Memory System V2 Ingestion Pipeline."""

from __future__ import annotations

import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from scout.chunker import ContextualChunker
from scout.ingest import (
    get_pg_connection,
    ingest_directory,
    ingest_document,
    validate_allowed_depts,
)
from scout.parsers import ParsedDocument, ParsedSection, parse_file
from tests.fakes import FakeEmbedder


def test_parse_markdown_file(tmp_path: Path) -> None:
    sample = tmp_path / "rfc101.md"
    sample.write_text(
        "# Overview\n\nSome overview text.\n\n## Details\n\nDeep technical detail."
    )

    doc = parse_file(sample, base_dir=tmp_path)
    assert doc.title == "Rfc101"
    assert doc.source_uri == "rfc101.md"
    assert len(doc.sections) == 2
    assert doc.sections[0].loc == "Section Overview"
    assert "Some overview text." in doc.sections[0].text
    assert doc.sections[1].loc == "Section Details"
    assert "Deep technical detail." in doc.sections[1].text


def test_parse_csv_file(tmp_path: Path) -> None:
    csv_file = tmp_path / "slo.csv"
    csv_file.write_text(
        "model,p95_ms,cost\ngpt-4o,250,5.0\nclaude-3-5-sonnet,220,3.0\n"
    )

    doc = parse_file(csv_file, base_dir=tmp_path)
    assert doc.title == "Slo"
    assert len(doc.sections) == 1
    assert "Rows" in doc.sections[0].loc
    assert "model: gpt-4o" in doc.sections[0].text
    assert "model: claude-3-5-sonnet" in doc.sections[0].text


def test_contextual_chunking() -> None:
    doc = ParsedDocument(
        title="TCP",
        source_uri="raw/rfcs/tcp.md",
        sections=[
            ParsedSection(
                text="TCP provides reliable, ordered, and error-checked delivery of a stream of octets.",
                loc="Section 1",
            ),
            ParsedSection(
                text="The transmission control protocol is used widely in IP networks.",
                loc="Section 2",
            ),
        ],
    )
    chunker = ContextualChunker(max_chunk_chars=1000, overlap_chars=100)
    chunks = chunker.chunk_document(doc)

    assert len(chunks) == 2
    for chunk in chunks:
        assert "[Document: TCP | Source: raw/rfcs/tcp.md" in chunk.context_prefix
        assert chunk.contextual_text.startswith("[Document:")


def test_fake_embedder_dimension() -> None:
    embedder = FakeEmbedder(dim=1024)
    res = embedder.embed_texts(["hello world", "protocol specification"])
    assert len(res) == 2
    assert len(res[0]) == 1024
    assert len(res[1]) == 1024


def test_allowed_departments_are_canonical_document_acls() -> None:
    assert validate_allowed_depts(["infra", "all", "infra"]) == ["infra", "all"]
    for invalid in ([], ["unknown"], [""], ["ALL"]):
        with pytest.raises(ValueError):
            validate_allowed_depts(invalid)


@pytest.mark.asyncio
async def test_embedding_failure_happens_before_database_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Source\n\nContent", encoding="utf-8")

    class WrongCardinalityEmbedder(FakeEmbedder):
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return []

    conn = MagicMock()
    with pytest.raises(Exception, match="cardinality"):
        await ingest_document(
            source,
            ["infra"],
            conn=conn,
            embedder=WrongCardinalityEmbedder(),
            base_dir=tmp_path,
        )
    conn.transaction.assert_not_called()


@pytest.mark.asyncio
async def test_document_insert_failure_rolls_back_last_good_rows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Source\n\n" + ("content " * 200), encoding="utf-8")

    class TransactionalConnection:
        def __init__(self) -> None:
            self.state: dict[str, Any] = {"title": "old", "chunks": ["old chunk"]}

        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            snapshot = {
                "title": self.state["title"],
                "chunks": list(self.state["chunks"]),
            }
            try:
                yield
            except Exception:
                self.state = snapshot
                raise

        async def fetchrow(self, _query: str, *_args: object) -> dict[str, int]:
            self.state["title"] = "new"
            return {"doc_id": 1}

        async def execute(self, query: str, *_args: object) -> str:
            if query.startswith("DELETE FROM rag_chunks"):
                self.state["chunks"] = []
                return "DELETE 1"
            if "INSERT INTO rag_chunks" in query:
                raise RuntimeError("synthetic insert failure")
            return "OK"

        async def close(self) -> None:
            return None

    conn = TransactionalConnection()
    observed: list[str] = []

    def observe(stage: str, _details: object) -> None:
        observed.append(stage)

    with pytest.raises(RuntimeError, match="insert failure"):
        await ingest_document(
            source,
            ["infra"],
            conn=conn,
            embedder=FakeEmbedder(),
            base_dir=tmp_path,
            stage_observer=observe,
        )
    assert conn.state == {"title": "old", "chunks": ["old chunk"]}
    assert observed == [
        "chunk_complete",
        "embed_request_sent",
        "embed_response_received",
    ]


@pytest.mark.asyncio
async def test_directory_batch_rolls_back_earlier_files_on_later_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "a.md").write_text("first", encoding="utf-8")
    (raw_dir / "b.md").write_text("second", encoding="utf-8")

    class BatchConnection:
        def __init__(self) -> None:
            self.rows = ["last-good"]
            self.closed = False

        async def fetch(self, _sql: str, *_args: object) -> list[object]:
            # No document carries a capability fingerprint, which is the
            # backward-compatible case: ingestion proceeds rather than refusing.
            return []

        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            snapshot = list(self.rows)
            try:
                yield
            except Exception:
                self.rows = snapshot
                raise

        async def close(self) -> None:
            self.closed = True

    conn = BatchConnection()

    async def fake_get_connection(_env: object = None) -> Any:
        # `get_pg_connection` now takes the resolved configuration, so a caller
        # that already loaded `.env` does not lose it to an `os.environ` read.
        return conn

    async def fake_ingest(file_path: Path, **_kwargs: object) -> dict[str, Any]:
        conn.rows.append(file_path.name)
        if file_path.name == "b.md":
            raise RuntimeError("later file failed")
        return {"source_uri": file_path.name, "status": "ingested_ok"}

    monkeypatch.setattr("scout.ingest.get_pg_connection", fake_get_connection)
    monkeypatch.setattr("scout.ingest.ingest_document", fake_ingest)

    with pytest.raises(RuntimeError, match="later file"):
        await ingest_directory(raw_dir, ["infra"], reconcile=False)
    assert conn.rows == ["last-good"]
    assert conn.closed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingest_document_to_postgres() -> None:
    """Integration test verifying end-to-end ingestion and idempotency in Postgres."""
    conn = await get_pg_connection()
    embedder = FakeEmbedder()

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(
            "# Ingestion Test\n\nThis is a live integration test for Postgres V2 ingestion."
        )
        temp_path = Path(f.name)

    try:
        # First Ingestion
        res1 = await ingest_document(
            file_path=temp_path,
            allowed_depts=["ai_eng", "all"],
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
        assert doc_row["allowed_depts"] == ["ai_eng", "all"]

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
            allowed_depts=["ai_eng", "blueteam"],
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_deletions() -> None:
    """Integration test verifying deletion reconciliation purges missing files from Postgres."""
    conn = await get_pg_connection()

    with tempfile.TemporaryDirectory() as tmp_dir:
        dir_path = Path(tmp_dir) / "test_sandbox_raw"
        dir_path.mkdir()
        file1 = dir_path / "doc1.md"
        file1.write_text("# Doc 1\nContent of doc 1.")

        embedder = FakeEmbedder()

        try:
            from scout.ingest import ingest_directory, reconcile_deletions

            results = await ingest_directory(
                dir_path=dir_path,
                allowed_depts=["all"],
                dry_run=False,
                reconcile=False,
                embedder=embedder,
            )
            assert len(results) == 1

            # Remove file1 from disk
            file1.unlink()

            # Reconcile deletions
            purged = await reconcile_deletions(
                dir_path=dir_path,
                conn=conn,
            )
            assert len(purged) == 1
            assert "doc1.md" in purged[0]
        finally:
            await conn.close()


# ── extraction outcome, recorded per document (leaf-3.2 · raw-tier visibility) ─


@pytest.mark.asyncio
async def test_ingest_records_what_the_parse_actually_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The document row carries the outcome, not only the capability.

    `capability_fingerprint` is byte-identical whether the describer answered or
    refused — by design, since the extractor was available either way. Without a
    separate outcome the index cannot answer "did this document arrive whole?",
    which is how a 7-figure paper sat in the corpus with zero figures described
    and nothing said so.
    """
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4 fake")

    degraded = ParsedDocument(
        source_uri="raw/papers/paper.pdf",
        title="Paper",
        sections=[ParsedSection(loc="p.1", text="body " * 200)],
        metadata={
            "figures_status": "failed",
            "figure_count": 7,
            "figures_described": 0,
            "tables_status": "ok",
        },
    )
    monkeypatch.setattr("scout.ingest.parse_file", lambda *_a, **_k: degraded)

    written: list[tuple[str, Any]] = []

    class RecordingConnection:
        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            yield

        async def fetchrow(self, query: str, *args: object) -> dict[str, int]:
            if "INSERT INTO rag_documents" in query:
                written.append((query, args))
            return {"doc_id": 1}

        async def execute(self, _query: str, *_args: object) -> str:
            return "OK"

        async def close(self) -> None:
            return None

    result = await ingest_document(
        source,
        ["infra"],
        conn=RecordingConnection(),
        embedder=FakeEmbedder(),
        base_dir=tmp_path,
    )

    assert written, "no document row was written"
    query, args = written[0]
    assert "extraction_status" in query
    recorded = json.loads(str(args[4]))
    assert recorded["complete"] is False
    assert recorded["incomplete"] == ["figures"]
    assert recorded["extractors"] == {"figures": "failed", "tables": "ok"}
    assert recorded["figure_count"] == 7
    assert recorded["figures_described"] == 0

    # The caller learns it too, without having to query the index back.
    assert result["status"] == "ingested_ok"
    assert result["extraction_status"]["complete"] is False


@pytest.mark.asyncio
async def test_a_whole_document_records_a_complete_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not call every document degraded, or it says nothing."""
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4 fake")
    whole = ParsedDocument(
        source_uri="raw/papers/paper.pdf",
        title="Paper",
        sections=[ParsedSection(loc="p.1", text="body " * 200)],
        metadata={
            "figures_status": "ok",
            "figure_count": 7,
            "figures_described": 7,
            "tables_status": "no_evidence",
        },
    )
    monkeypatch.setattr("scout.ingest.parse_file", lambda *_a, **_k: whole)

    class Connection:
        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            yield

        async def fetchrow(self, _query: str, *_args: object) -> dict[str, int]:
            return {"doc_id": 1}

        async def execute(self, _query: str, *_args: object) -> str:
            return "OK"

        async def close(self) -> None:
            return None

    result = await ingest_document(
        source, ["infra"], conn=Connection(), embedder=FakeEmbedder(), base_dir=tmp_path
    )
    assert result["extraction_status"] == {
        "extractors": {"figures": "ok", "tables": "no_evidence"},
        "figure_count": 7,
        "figures_described": 7,
        "complete": True,
    }


def test_a_document_with_nothing_structural_is_complete_by_construction() -> None:
    """Markdown loses nothing, so it must not be reported as degraded."""
    from scout.parsers import extraction_state

    assert extraction_state({"type": "markdown"}) == {
        "extractors": {},
        "complete": True,
    }


# ── the vision route comes from the resolved configuration ─────────────────
#
# `snpmemory ingest` resolves the project `.env` into `cfg.values` and hands it
# to `ingest_directory` as `env`. The vision route used to be decided from
# `os.environ` inside the parser instead, so a host shell that had not exported
# LITELLM_* recorded every image as `unconfigured` -- and, because an image
# with no description yields no chunks, purged the good rows an earlier run had
# published. These tests put the route ONLY in the mapping.

_VISION_ENV = {
    "LITELLM_BASE_URL": "http://gateway.from-dotenv:4000",
    "LITELLM_MASTER_KEY": "sk-from-dotenv",
    "LITELLM_VLM_MODEL": "vlm-from-dotenv",
}


def _without_vision_route_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY", "LITELLM_VLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _recording_vlm(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace only the network call; the route decision stays real."""
    calls: list[dict[str, Any]] = []

    def fake_vlm(path: Path, uri: str, **kwargs: Any) -> str:
        calls.append({"uri": uri, **kwargs})
        return "## Visual Overview\n\n" + ("A gateway fronts the index. " * 40)

    monkeypatch.setattr("scout.parsers.extract_image_via_vlm", fake_vlm)
    return calls


@pytest.mark.asyncio
async def test_directory_ingest_takes_the_vision_route_from_the_env_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _without_vision_route_in_process(monkeypatch)
    calls = _recording_vlm(monkeypatch)

    raw = tmp_path / "raw"
    (raw / "images").mkdir(parents=True)
    (raw / "images" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    statements: list[str] = []

    class Connection:
        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            yield

        async def fetch(self, _query: str, *_args: object) -> list[Any]:
            return []

        async def fetchrow(self, _query: str, *_args: object) -> dict[str, int]:
            return {"doc_id": 1}

        async def execute(self, query: str, *_args: object) -> str:
            statements.append(query)
            return "DELETE 1" if query.lstrip().startswith("DELETE") else "OK"

        async def close(self) -> None:
            return None

    async def connect(_env: Any = None) -> Connection:
        return Connection()

    monkeypatch.setattr("scout.ingest.get_pg_connection", connect)

    results = await ingest_directory(
        raw,
        ["infra"],
        reconcile=False,
        embedder=FakeEmbedder(),
        env=_VISION_ENV,
    )

    (row,) = results
    assert row["source_uri"] == "raw/images/diagram.png"
    assert row["status"] != "purged_empty", "a good image row was purged"
    assert row["chunks_count"] > 0
    assert not any("DELETE FROM rag_documents" in query for query in statements)
    assert calls and calls[0]["base_url"] == "http://gateway.from-dotenv:4000"
    assert calls[0]["api_key"] == "sk-from-dotenv"
    assert calls[0]["model"] == "vlm-from-dotenv"


def test_pdf_figures_take_the_vision_route_from_the_env_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout import parsers
    from scout.pdf_structure import ExtractedFigure

    _without_vision_route_in_process(monkeypatch)
    calls = _recording_vlm(monkeypatch)
    figure = ExtractedFigure(
        page=2,
        number="1",
        caption="Figure 1: the pipeline",
        name="Im1",
        data=b"\x89PNG\r\n\x1a\n",
        digest="ab" * 32,
    )
    monkeypatch.setattr("scout.pdf_structure.extract_figures", lambda _p: [figure])

    metadata: dict[str, Any] = {}
    sections = parsers._pdf_figure_sections(
        tmp_path / "paper.pdf",
        "raw/papers/paper.pdf",
        metadata,
        None,
        env=_VISION_ENV,
    )

    assert metadata["figures_status"] == parsers.FIGURES_OK
    assert len(sections) == 1
    assert calls[0]["base_url"] == "http://gateway.from-dotenv:4000"


def test_an_env_mapping_without_the_route_is_unconfigured_whatever_the_shell_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mapping is the configuration; the shell does not leak back in."""
    from scout.parsers import VLM_STATUS_UNCONFIGURED

    monkeypatch.setenv("LITELLM_BASE_URL", "http://shell-only:4000")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-shell-only")
    calls = _recording_vlm(monkeypatch)
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    doc = parse_file(image, base_dir=tmp_path, env={})

    assert doc.metadata["vlm_status"] == VLM_STATUS_UNCONFIGURED
    assert calls == []
