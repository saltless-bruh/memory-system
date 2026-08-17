"""Tests for the sync-job auto-ingest (T-3.2, R-6.1). urllib mocked; no network."""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from scout.sync_job import (
    HttpRagIndexer,
    IndexOutcome,
    PgVectorDirectIndexer,
    RagIndexer,
    sync_once,
    watch,
)


def test_pgvector_direct_indexer_is_a_rag_indexer() -> None:
    assert isinstance(PgVectorDirectIndexer(), RagIndexer)


async def test_pgvector_direct_indexer_runs_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_ingest(dir_path: Path, allowed_depts: list[str], dry_run: bool) -> list[dict[str, Any]]:
        calls.append({"dir_path": dir_path, "allowed_depts": allowed_depts, "dry_run": dry_run})
        return [{"status": "ingested_ok", "source_uri": "raw/test.md"}]

    monkeypatch.setattr("scout.ingest.ingest_directory", fake_ingest)
    indexer = PgVectorDirectIndexer(raw_dir=Path("raw/test"), allowed_depts=("redteam", "all"))
    outcome = await indexer.index()

    assert outcome.ok is True
    assert outcome.status == "ingested_1_files"
    assert len(calls) == 1
    assert calls[0]["dir_path"] == Path("raw/test")
    assert calls[0]["allowed_depts"] == ["redteam", "all"]


# ── fakes ─────────────────────────────────────────────────────────────────
@dataclass
class FakeIndexer:
    """A RagIndexer that counts calls and returns a canned outcome."""

    outcome: IndexOutcome = field(
        default_factory=lambda: IndexOutcome(ok=True, status="indexed")
    )
    calls: int = 0

    async def index(self) -> IndexOutcome:
        self.calls += 1
        return self.outcome


async def _batches(n: int) -> AsyncIterator[object]:
    """A finite change stream of `n` batches (stands in for the file watch)."""
    for i in range(n):
        yield {("modified", f"raw/f{i}.pdf")}


def test_fake_indexer_is_a_rag_indexer() -> None:
    assert isinstance(FakeIndexer(), RagIndexer)


# ── HttpRagIndexer (wire) ─────────────────────────────────────────────────
@pytest.fixture
def patched_urlopen(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch urllib.request.urlopen to capture the request and return a body."""
    seen: dict[str, Any] = {"status": "indexed", "raw_dir": "/data/raw"}

    class _CM:
        def __enter__(self) -> io.BytesIO:
            return io.BytesIO(
                json.dumps({"status": seen["status"], "raw_dir": "/data/raw"}).encode()
            )

        def __exit__(self, *a: object) -> None:
            return None

    def fake_urlopen(req: Any, timeout: float | None = None) -> _CM:
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["timeout"] = timeout
        return _CM()

    monkeypatch.setattr("scout.sync_job.urllib.request.urlopen", fake_urlopen)
    return seen


async def test_http_indexer_posts_to_index_and_reports_ok(
    patched_urlopen: dict[str, Any],
) -> None:
    outcome = await HttpRagIndexer(base_url="http://rag:8000", timeout=5.0).index()
    assert outcome == IndexOutcome(ok=True, status="indexed")
    assert patched_urlopen["url"].endswith("/index")
    assert patched_urlopen["method"] == "POST"
    assert patched_urlopen["timeout"] == 5.0


async def test_http_indexer_reports_not_ok_on_unexpected_status(
    patched_urlopen: dict[str, Any],
) -> None:
    patched_urlopen["status"] = "error"
    outcome = await HttpRagIndexer().index()
    assert outcome.ok is False and outcome.status == "error"


# ── sync_once: regen gating ───────────────────────────────────────────────
async def test_sync_once_runs_sync_regen_after_success() -> None:
    hits: list[str] = []
    out = await sync_once(FakeIndexer(), regen=lambda: hits.append("regen"))
    assert out.ok is True
    assert hits == ["regen"]


async def test_sync_once_awaits_async_regen() -> None:
    hits: list[str] = []

    async def regen() -> None:
        hits.append("async-regen")

    await sync_once(FakeIndexer(), regen=regen)
    assert hits == ["async-regen"]


async def test_sync_once_skips_regen_when_index_failed() -> None:
    hits: list[str] = []
    indexer = FakeIndexer(outcome=IndexOutcome(ok=False, status="error"))
    out = await sync_once(indexer, regen=lambda: hits.append("regen"))
    assert out.ok is False
    assert hits == []  # a failed index must not restamp the wiki index


async def test_sync_once_without_regen_is_fine() -> None:
    indexer = FakeIndexer()
    out = await sync_once(indexer)
    assert out.ok is True and indexer.calls == 1


# ── watch: one reindex per change batch ───────────────────────────────────
async def test_watch_reindexes_once_per_batch() -> None:
    indexer = FakeIndexer()
    handled = await watch(indexer, changes=_batches(3))
    assert handled == 3
    assert indexer.calls == 3  # dropping files -> RAG sees them, no manual step


async def test_watch_threads_regen_through_each_batch() -> None:
    indexer = FakeIndexer()
    hits: list[int] = []
    await watch(indexer, changes=_batches(2), regen=lambda: hits.append(1))
    assert sum(hits) == 2


async def test_watch_empty_stream_does_nothing() -> None:
    indexer = FakeIndexer()
    assert await watch(indexer, changes=_batches(0)) == 0
    assert indexer.calls == 0


async def test_watch_requires_changes_or_raw_dir() -> None:
    with pytest.raises(ValueError, match="changes.*raw_dir"):
        await watch(FakeIndexer())


async def test_watch_with_raw_dir_uses_the_file_watch_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raw_dir (no explicit stream) routes through the watchfiles adapter."""
    seen: dict[str, Any] = {}

    def fake_awatch(raw_dir: Any, stop: Any) -> AsyncIterator[object]:
        seen["raw_dir"] = raw_dir
        return _batches(2)

    monkeypatch.setattr("scout.sync_job._awatch_raw", fake_awatch)
    indexer = FakeIndexer()
    handled = await watch(indexer, raw_dir=Path("raw"))
    assert handled == 2 and indexer.calls == 2
    assert seen["raw_dir"] == Path("raw")
