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


# ── crash-loop containment (2026-08-24 incident) ──────────────────────────
#
# On 2026-08-24 `sync-job` restarted 238 times because a transient dependency
# failure exited the process, and Docker's restart backoff resets after the
# container survives 10 seconds — which this one always did, since DNS
# timeouts are slow. The orchestrator's backoff could never accumulate, so the
# process must hold its own.


async def test_cold_start_retries_a_retryable_failure_instead_of_exiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_async_main` must survive a cold start that keeps failing transiently.

    `sync_once` is patched rather than driven through a fake indexer: it does
    its own bounded retry, so scripted outcomes handed to an indexer are
    consumed by that inner loop and never reach the outer one under test here.
    """
    marker = tmp_path / "ready"
    delays: list[float] = []
    outcomes = [
        IndexOutcome(False, "error:EmbeddingError", retryable=True),
        IndexOutcome(False, "error:EmbeddingError", retryable=True),
        IndexOutcome(True, "ingested_1_files"),
    ]
    seen_marker: list[bool] = []

    async def scripted_sync_once(indexer: Any, **kwargs: Any) -> IndexOutcome:
        seen_marker.append(marker.exists())
        return outcomes[min(len(seen_marker) - 1, len(outcomes) - 1)]

    async def record(delay: float) -> None:
        delays.append(delay)

    async def stop_watch(*args: object, **kwargs: object) -> int:
        return 0

    monkeypatch.setattr("scout.sync_job.sync_once", scripted_sync_once)
    monkeypatch.setattr("scout.sync_job.watch", stop_watch)

    await _async_main(
        FakeIndexer(), tmp_path, marker, base_delay=5.0, max_delay=300.0, sleep=record
    )

    assert len(seen_marker) == 3
    # Readiness stays cleared for every failing attempt: the container is alive
    # and honestly reporting unhealthy, not pretending to work.
    assert seen_marker == [False, False, False]
    assert marker.exists()
    assert delays == [5.0, 10.0]


async def test_cold_start_backoff_is_capped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    delays: list[float] = []

    async def always_failing(indexer: Any, **kwargs: Any) -> IndexOutcome:
        return IndexOutcome(False, "error:EmbeddingError", retryable=True)

    async def record(delay: float) -> None:
        delays.append(delay)
        if len(delays) >= 8:
            raise RuntimeError("stop")

    monkeypatch.setattr("scout.sync_job.sync_once", always_failing)
    with pytest.raises(RuntimeError, match="stop"):
        await _async_main(
            FakeIndexer(),
            tmp_path,
            tmp_path / "ready",
            base_delay=5.0,
            max_delay=60.0,
            sleep=record,
        )
    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0, 60.0]


async def test_cold_start_still_exits_on_a_non_retryable_failure(
    tmp_path: Path,
) -> None:
    """A missing ACL policy is a configuration fault. Retrying it is wrong."""
    marker = tmp_path / "ready"
    indexer = FakeIndexer(IndexOutcome(False, "error:AclPolicyError"))

    async def never(delay: float) -> None:  # pragma: no cover - must not run
        raise AssertionError("a permanent failure must not be retried")

    with pytest.raises(SystemExit) as caught:
        await _async_main(indexer, tmp_path, marker, sleep=never)
    assert caught.value.code == 1
    assert not marker.exists()


async def test_watch_failure_carries_the_outcome_retryability() -> None:
    indexer = FakeIndexer(IndexOutcome(False, "error:EmbeddingError", retryable=True))
    with pytest.raises(SyncFailure) as caught:
        await watch(indexer, changes=_batches(1))
    assert caught.value.retryable is True

    permanent = FakeIndexer(
        IndexOutcome(False, "error:AclPolicyError", retryable=False)
    )
    with pytest.raises(SyncFailure) as caught:
        await watch(permanent, changes=_batches(1))
    assert caught.value.retryable is False


async def test_a_retryable_watched_failure_is_retried_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dependency that dies mid-watch must not take the container with it."""
    marker = tmp_path / "ready"
    delays: list[float] = []
    watches: list[int] = []

    async def flaky_watch(*args: object, **kwargs: object) -> int:
        watches.append(1)
        if len(watches) == 1:
            assert marker.exists()
            raise SyncFailure("watched synchronization failed", retryable=True)
        return 0

    async def record(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("scout.sync_job.watch", flaky_watch)
    await _async_main(
        FakeIndexer(), tmp_path, marker, base_delay=5.0, max_delay=300.0, sleep=record
    )

    assert len(watches) == 2
    assert delays == [5.0]
    assert marker.exists()


def test_httpx_status_errors_are_classified_like_urllib_ones() -> None:
    """`aembed_texts` raises httpx errors; the classifier must not ignore them."""
    from scout.chunker import EmbeddingError

    def wrapped(code: int) -> EmbeddingError:
        request = httpx.Request("POST", "https://gateway.invalid/embeddings")
        error = httpx.HTTPStatusError(
            "synthetic", request=request, response=httpx.Response(code, request=request)
        )
        try:
            raise EmbeddingError("redacted") from error
        except EmbeddingError as exc:
            return exc

    assert _is_transient(wrapped(500))
    assert _is_transient(wrapped(502))
    assert _is_transient(wrapped(429))
    assert _is_transient(wrapped(408))
    assert not _is_transient(wrapped(400))
    assert not _is_transient(wrapped(404))


# ── T6.5: a retry must not re-parse a corpus that has not changed ──────────


def test_a_retry_after_a_failed_embed_does_not_reparse_the_corpus(
    tmp_path: Path,
) -> None:
    """The failure that actually occurs is at the embed step.

    Re-parsing every document before each retry costs the whole corpus's parse
    work to reach the one call that failed, and it scales with the corpus rather
    than with the failure. Observed during the Tier 0 outage test as
    `Table extraction unavailable …` printed three times per backoff cycle.
    """
    from scout.ingest import ParseCache

    corpus = tmp_path / "raw"
    corpus.mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (corpus / name).write_text(f"# {name}\n\nbody of {name}\n", encoding="utf-8")

    cache = ParseCache()
    for _attempt in range(3):
        for name in ("a.md", "b.md", "c.md"):
            cache.parsed(corpus / name, base_dir=tmp_path)

    # Three documents, three parses — not nine.
    assert cache.parses == 3


def test_a_changed_file_is_parsed_again(tmp_path: Path) -> None:
    """Keyed by identity, not by path: a stale reuse must be impossible."""
    import os

    from scout.ingest import ParseCache

    source = tmp_path / "a.md"
    source.write_text("# a\n\noriginal\n", encoding="utf-8")

    cache = ParseCache()
    first = cache.parsed(source, base_dir=tmp_path)
    assert "original" in first.full_text

    source.write_text("# a\n\nrewritten entirely\n", encoding="utf-8")
    os.utime(source, ns=(0, 0))  # force a different mtime_ns

    second = cache.parsed(source, base_dir=tmp_path)
    assert cache.parses == 2
    assert "rewritten entirely" in second.full_text


def test_an_unreadable_file_is_never_served_from_cache(tmp_path: Path) -> None:
    """Without an identity there is no safe reuse, so parse and let it raise.

    The cache must not turn a missing file into a cache miss it then swallows —
    the parser's own error is the right answer and has to reach the caller.
    """
    from scout.ingest import ParseCache
    from scout.parsers import ParserError

    cache = ParseCache()
    with pytest.raises(ParserError):
        cache.parsed(tmp_path / "absent.md", base_dir=tmp_path)
    assert cache.parses == 1, "it must have attempted a real parse"


def test_a_successful_index_clears_the_cache(tmp_path: Path) -> None:
    """A cache that outlived its cycle would hold a corpus-worth of text."""
    import asyncio

    from scout.ingest import ParseCache
    from scout.sync_job import PgVectorDirectIndexer

    corpus = tmp_path / "raw"
    corpus.mkdir()
    (corpus / ".acl.yaml").write_text(
        'version: 1\nrules:\n  - path: "**"\n    departments: [ai_eng]\n',
        encoding="utf-8",
    )
    (corpus / "a.md").write_text("# a\n\nbody\n", encoding="utf-8")

    cache = ParseCache()
    indexer = PgVectorDirectIndexer(raw_dir=corpus, parse_cache=cache)

    async def _fake_ingest(**_kwargs: object) -> list[dict[str, object]]:
        cache.parsed(corpus / "a.md", base_dir=tmp_path)
        return [{"source_uri": "raw/a.md", "chunks_count": 1}]

    import scout.ingest as ingest_module

    original = ingest_module.ingest_directory
    ingest_module.ingest_directory = _fake_ingest  # type: ignore[assignment]
    try:
        outcome = asyncio.run(indexer.index())
    finally:
        ingest_module.ingest_directory = original  # type: ignore[assignment]

    assert outcome.ok
    assert cache._entries == {}, "a successful cycle must not retain parses"
