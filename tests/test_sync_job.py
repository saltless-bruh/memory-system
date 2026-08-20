"""Tests for the sync-job auto-ingest (T-3.2, R-6.1). urllib mocked; no network."""

from __future__ import annotations

import urllib.error
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any

import httpx
import pytest

from scout.ingest import DEFAULT_ACL_FILENAME
from scout.sync_job import (
    HttpRagIndexer,
    IndexOutcome,
    PgVectorDirectIndexer,
    RagIndexer,
    SyncFailure,
    _async_main,
    _is_transient,
    sync_once,
    watch,
)


def test_pgvector_direct_indexer_is_a_rag_indexer() -> None:
    assert isinstance(PgVectorDirectIndexer(), RagIndexer)


def test_embedding_http_4xx_is_permanent_but_5xx_is_transient() -> None:
    from scout.chunker import EmbeddingError

    def wrapped(code: int) -> EmbeddingError:
        error = urllib.error.HTTPError(
            url="https://gateway.invalid/embeddings",
            code=code,
            msg="synthetic",
            hdrs=Message(),
            fp=None,
        )
        try:
            raise EmbeddingError("redacted") from error
        except EmbeddingError as exc:
            return exc

    assert not _is_transient(wrapped(401))
    assert not _is_transient(wrapped(422))
    assert _is_transient(wrapped(503))


async def test_pgvector_direct_indexer_runs_ingest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_ingest(
        dir_path: Path,
        acl: Any,
        dry_run: bool = False,
        embedder: Any = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        calls.append(
            {
                "dir_path": dir_path,
                "acl": acl,
                "dry_run": dry_run,
                "embedder": embedder,
            }
        )
        return [{"status": "ingested_ok", "source_uri": "raw/test.md"}]

    monkeypatch.setattr("scout.ingest.ingest_directory", fake_ingest)

    # The indexer carries no department of its own; it resolves one from the
    # ACL map beside the corpus (audit M1 — the old hardcoded ("all",) default).
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / DEFAULT_ACL_FILENAME).write_text(
        'version: 1\nrules:\n  - path: "reports/**"\n    departments: [redteam]\n',
        encoding="utf-8",
    )

    indexer = PgVectorDirectIndexer(raw_dir=raw_dir)
    outcome = await indexer.index()

    assert outcome.ok is True
    assert outcome.status == "ingested_1_files"
    assert len(calls) == 1
    assert calls[0]["dir_path"] == raw_dir
    assert calls[0]["acl"].departments_for(raw_dir / "reports" / "x.md") == ["redteam"]
    # a file the map does not cover resolves to None, never to `all`
    assert calls[0]["acl"].departments_for(raw_dir / "unmapped" / "y.md") is None


async def test_pgvector_direct_indexer_publishes_nothing_without_a_readable_acl(
    tmp_path: Path,
) -> None:
    """A missing policy must stop the ingest, never fall back to `all` (M1)."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()  # deliberately no .acl.yaml

    outcome = await PgVectorDirectIndexer(raw_dir=raw_dir).index()

    assert outcome.ok is False
    assert outcome.status == "error:AclPolicyError"


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
def http_transport() -> tuple[dict[str, Any], httpx.MockTransport]:
    seen: dict[str, Any] = {"status": "indexed", "raw_dir": "/data/raw"}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(
            200,
            json={"status": seen["status"], "raw_dir": "/data/raw"},
        )

    return seen, httpx.MockTransport(handler)


async def test_http_indexer_posts_to_index_and_reports_ok(
    http_transport: tuple[dict[str, Any], httpx.MockTransport],
) -> None:
    seen, transport = http_transport
    outcome = await HttpRagIndexer(
        base_url="http://rag:8000", timeout=5.0, transport=transport
    ).index()
    assert outcome == IndexOutcome(ok=True, status="indexed")
    assert seen["url"].endswith("/index")
    assert seen["method"] == "POST"


async def test_http_indexer_reports_not_ok_on_unexpected_status(
    http_transport: tuple[dict[str, Any], httpx.MockTransport],
) -> None:
    seen, transport = http_transport
    seen["status"] = "error"
    outcome = await HttpRagIndexer(transport=transport).index()
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


async def test_sync_once_retries_only_retryable_failures() -> None:
    outcomes = iter(
        [
            IndexOutcome(False, "network", retryable=True),
            IndexOutcome(False, "network", retryable=True),
            IndexOutcome(True, "indexed"),
        ]
    )

    @dataclass
    class SequenceIndexer:
        calls: int = 0

        async def index(self) -> IndexOutcome:
            self.calls += 1
            return next(outcomes)

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    indexer = SequenceIndexer()
    result = await sync_once(indexer, base_delay=0.5, sleep=fake_sleep)
    assert result.ok
    assert indexer.calls == 3
    assert delays == [0.5, 1.0]


async def test_sync_once_does_not_retry_permanent_failure() -> None:
    indexer = FakeIndexer(IndexOutcome(False, "invalid", retryable=False))
    result = await sync_once(indexer)
    assert not result.ok
    assert indexer.calls == 1


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


async def test_watch_initial_sync_triggers_reindex_before_changes() -> None:
    indexer = FakeIndexer()
    hits: list[str] = []
    handled = await watch(
        indexer,
        changes=_batches(2),
        initial_sync=True,
        regen=lambda: hits.append("regen"),
    )
    # 1 initial sync + 2 stream batches = 3 total
    assert handled == 3
    assert indexer.calls == 3
    assert len(hits) == 3


async def test_watch_initial_sync_with_empty_stream() -> None:
    indexer = FakeIndexer()
    handled = await watch(
        indexer,
        changes=_batches(0),
        initial_sync=True,
    )
    # 1 initial sync + 0 stream batches = 1 total
    assert handled == 1
    assert indexer.calls == 1


async def test_watch_raises_after_failed_batch() -> None:
    indexer = FakeIndexer(IndexOutcome(False, "database", retryable=False))
    with pytest.raises(SyncFailure, match="watched"):
        await watch(indexer, changes=_batches(1))


async def test_async_main_executes_cold_start_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    order: list[str] = []

    async def fake_sync_once(indexer: Any, *, regen: Any = None) -> IndexOutcome:
        order.append("sync_once")
        return IndexOutcome(ok=True, status="indexed")

    async def fake_watch(
        indexer: Any,
        *,
        regen: Any = None,
        changes: Any = None,
        raw_dir: Any = None,
        stop: Any = None,
        initial_sync: bool = False,
    ) -> int:
        order.append(f"watch(initial_sync={initial_sync})")
        return 1

    monkeypatch.setattr("scout.sync_job.sync_once", fake_sync_once)
    monkeypatch.setattr("scout.sync_job.watch", fake_watch)

    indexer = FakeIndexer()
    await _async_main(indexer, Path("raw"), tmp_path / "ready")

    assert order == ["sync_once", "watch(initial_sync=False)"]


async def test_async_main_clears_readiness_on_cold_start_failure(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "ready"
    marker.write_text("stale", encoding="utf-8")
    indexer = FakeIndexer(IndexOutcome(False, "invalid", retryable=False))
    with pytest.raises(SystemExit) as caught:
        await _async_main(indexer, tmp_path, marker)
    assert caught.value.code == 1
    assert not marker.exists()


async def test_async_main_clears_readiness_on_watched_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "ready"

    async def failing_watch(*args: object, **kwargs: object) -> int:
        assert marker.exists()
        raise SyncFailure("watched synchronization failed")

    monkeypatch.setattr("scout.sync_job.watch", failing_watch)
    with pytest.raises(SystemExit) as caught:
        await _async_main(FakeIndexer(), tmp_path, marker)
    assert caught.value.code == 1
    assert not marker.exists()
