"""Tests for the sync-job auto-ingest (T-3.2, R-6.1). urllib mocked; no network."""

from __future__ import annotations

import asyncio
import contextlib
import json
import urllib.error
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest

from scout.ingest import DEFAULT_ACL_FILENAME
from scout.sync_job import (
    HttpRagIndexer,
    IndexOutcome,
    PgVectorDirectIndexer,
    RagIndexer,
    SyncFailure,
    WikiIndexer,
    _aggregate_readiness,
    _async_main,
    _emit_wiki_sync_stage,
    _is_transient,
    _PermanentSyncFailure,
    _supervise,
    _wiki_snapshot_commit,
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

    def fake_awatch(
        raw_dir: Any, stop: Any, *, recursive: bool = True
    ) -> AsyncIterator[object]:
        seen["raw_dir"] = raw_dir
        seen["recursive"] = recursive
        return _batches(2)

    monkeypatch.setattr("scout.sync_job._awatch_raw", fake_awatch)
    indexer = FakeIndexer()
    handled = await watch(indexer, raw_dir=Path("raw"))
    assert handled == 2 and indexer.calls == 2
    assert seen["raw_dir"] == Path("raw")
    assert seen["recursive"] is True


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
        recursive: bool = True,
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
        # Each watcher publishes its own marker; the service-level one is the
        # conjunction of them, maintained separately by `_aggregate_readiness`.
        assert marker.with_name(f"{marker.name}.raw").exists()
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

    raw_marker = marker.with_name(f"{marker.name}.raw")

    async def flaky_watch(*args: object, **kwargs: object) -> int:
        watches.append(1)
        if len(watches) == 1:
            # This watcher's own marker; the service-level one is the
            # conjunction of every watcher's, maintained separately.
            assert raw_marker.exists()
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


# ── WikiIndexer: the vault at the existing RagIndexer seam ─────────────────


@pytest.mark.asyncio
async def test_wiki_indexer_ingests_then_reconciles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The vault indexer must ingest pages and purge rows for deleted files."""
    calls: list[str] = []

    async def fake_ingest_wiki(
        wiki_dir: Path, **kwargs: object
    ) -> list[dict[str, object]]:
        calls.append("ingest")
        assert wiki_dir == tmp_path
        # These are the statuses the real pipeline emits -- `ingest_document`
        # returns "ingested_ok", and `ingest_wiki` returns "skipped_no_body"
        # for a page it cannot read a body from.
        return [
            {"source_uri": "a.md", "chunks_count": 3, "status": "ingested_ok"},
            {"source_uri": "b.md", "chunks_count": 0, "status": "skipped_no_body"},
        ]

    async def fake_reconcile(wiki_dir: Path, **kwargs: object) -> list[str]:
        calls.append("reconcile")
        return ["gone.md"]

    monkeypatch.setattr("scout.sync_job.ingest_wiki", fake_ingest_wiki)
    monkeypatch.setattr("scout.sync_job.reconcile_wiki_deletions", fake_reconcile)

    outcome = await WikiIndexer(wiki_dir=tmp_path).index()

    assert outcome.ok is True
    # Ingest must precede reconciliation: reconciling first would delete rows
    # for pages this very cycle is about to re-add.
    assert calls == ["ingest", "reconcile"]
    assert "1 indexed" in outcome.status
    assert "1 deleted" in outcome.status


@pytest.mark.asyncio
async def test_wiki_indexer_counts_the_status_the_pipeline_actually_emits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A count keyed to a status nothing emits reports zero forever."""
    from scout import ingest as ingest_module

    source = Path(ingest_module.__file__).read_text(encoding="utf-8")
    assert '"status": "ingested_ok"' in source

    async def fake_ingest_wiki(
        wiki_dir: Path, **kwargs: object
    ) -> list[dict[str, object]]:
        return [{"source_uri": f"p{i}.md", "status": "ingested_ok"} for i in range(4)]

    async def fake_reconcile(wiki_dir: Path, **kwargs: object) -> list[str]:
        return []

    monkeypatch.setattr("scout.sync_job.ingest_wiki", fake_ingest_wiki)
    monkeypatch.setattr("scout.sync_job.reconcile_wiki_deletions", fake_reconcile)

    outcome = await WikiIndexer(wiki_dir=tmp_path).index()
    assert "4 indexed" in outcome.status


def test_wiki_indexer_is_a_rag_indexer() -> None:
    assert isinstance(WikiIndexer(wiki_dir=Path("wiki")), RagIndexer)


@pytest.mark.asyncio
async def test_wiki_indexer_reports_a_transient_fault_as_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dependency outage must be retryable, not fatal (the 238-restart lesson)."""

    async def boom(wiki_dir: Path, **kwargs: object) -> list[dict[str, object]]:
        raise urllib.error.HTTPError(
            url="https://gateway.invalid/embeddings",
            code=503,
            msg="synthetic",
            hdrs=Message(),
            fp=None,
        )

    monkeypatch.setattr("scout.sync_job.ingest_wiki", boom)
    outcome = await WikiIndexer(wiki_dir=tmp_path).index()
    assert outcome.ok is False
    assert outcome.retryable is True


@pytest.mark.asyncio
async def test_wiki_indexer_reports_a_config_fault_as_permanent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing vault is a configuration fault; waiting cannot fix it."""

    async def boom(wiki_dir: Path, **kwargs: object) -> list[dict[str, object]]:
        raise FileNotFoundError("no such vault")

    monkeypatch.setattr("scout.sync_job.ingest_wiki", boom)
    outcome = await WikiIndexer(wiki_dir=tmp_path).index()
    assert outcome.ok is False
    assert outcome.retryable is False


@pytest.mark.asyncio
async def test_wiki_indexer_does_not_reconcile_after_a_failed_ingest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Purging after a failed rebuild empties the corpus it could not rebuild."""
    reconciled: list[str] = []

    async def boom(wiki_dir: Path, **kwargs: object) -> list[dict[str, object]]:
        raise OSError("connection refused")

    async def fake_reconcile(wiki_dir: Path, **kwargs: object) -> list[str]:
        reconciled.append("ran")
        return []

    monkeypatch.setattr("scout.sync_job.ingest_wiki", boom)
    monkeypatch.setattr("scout.sync_job.reconcile_wiki_deletions", fake_reconcile)

    outcome = await WikiIndexer(wiki_dir=tmp_path).index()
    assert outcome.ok is False
    assert reconciled == []


# ── two watchers, isolated failures ────────────────────────────────────────


class _StopSupervision(Exception):
    """Ends a supervision loop inside a test without killing the process."""


@pytest.mark.asyncio
async def test_one_watcher_failing_does_not_stop_the_other(tmp_path: Path) -> None:
    """A vault fault must not stall raw ingest, nor the reverse.

    Both corpora are independent; coupling their failures would mean one bad
    page in the vault silently stops the raw pipeline the compile tools read.
    """
    raw_marker = tmp_path / "raw.ready"
    wiki_marker = tmp_path / "wiki.ready"

    class AlwaysFails:
        async def index(self) -> IndexOutcome:
            return IndexOutcome(ok=False, status="down", retryable=True)

    healthy = FakeIndexer()
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)
        if len(slept) >= 2:
            raise _StopSupervision

    async def forever_watch(*args: object, **kwargs: object) -> int:
        await asyncio.Event().wait()
        return 0

    with (
        mock.patch("scout.sync_job.watch", forever_watch),
        contextlib.suppress(_StopSupervision),
    ):
        await asyncio.gather(
            _supervise(AlwaysFails(), tmp_path, wiki_marker, sleep=fake_sleep),
            _supervise(healthy, tmp_path, raw_marker, sleep=fake_sleep),
        )

    # The healthy watcher reached readiness even though its sibling never did.
    assert raw_marker.exists()
    assert not wiki_marker.exists()


@pytest.mark.asyncio
async def test_async_main_supervises_both_corpora_when_wiki_dir_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    supervised: list[Path] = []

    async def fake_supervise(
        indexer: object, source_dir: Path, marker: Path, **kwargs: object
    ) -> None:
        supervised.append(source_dir)

    async def no_aggregate(marker: Path, children: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("scout.sync_job._supervise", fake_supervise)
    monkeypatch.setattr("scout.sync_job._aggregate_readiness", no_aggregate)
    await _async_main(
        FakeIndexer(),
        raw_dir=tmp_path / "raw",
        wiki_dir=tmp_path / "wiki",
        readiness_path=tmp_path / "ready",
    )
    assert supervised == [tmp_path / "raw", tmp_path / "wiki"]


@pytest.mark.asyncio
async def test_the_vault_watcher_follows_the_publication_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The replica publishes by swapping a symlink, not by editing pages.

    `host-sync` materialises `snapshots/<commit>/` and then atomically retargets
    `current`. An inotify watch on `current/wiki` is bound to the *old*
    snapshot's inode and receives nothing -- measured: 0 batches through a full
    republish. The directory holding the pointer does see it, so the vault
    watcher must watch that instead, and non-recursively, or every file of every
    materialised snapshot re-triggers the whole corpus.
    """
    watched: list[tuple[Path, bool]] = []

    async def fake_supervise(
        indexer: object,
        source_dir: Path,
        marker: Path,
        *,
        recursive: bool = True,
        **kwargs: object,
    ) -> None:
        watched.append((source_dir, recursive))

    async def no_aggregate(marker: Path, children: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("scout.sync_job._supervise", fake_supervise)
    monkeypatch.setattr("scout.sync_job._aggregate_readiness", no_aggregate)
    await _async_main(
        FakeIndexer(),
        raw_dir=tmp_path / "raw",
        wiki_dir=tmp_path / "replica" / "current" / "wiki",
        wiki_watch_dir=tmp_path / "replica",
        readiness_path=tmp_path / "ready",
    )
    assert watched == [
        (tmp_path / "raw", True),
        (tmp_path / "replica", False),
    ]


@pytest.mark.asyncio
async def test_the_vault_indexer_still_reads_through_the_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Watching the pointer must not change *what* gets indexed."""
    built: list[Path] = []

    async def fake_supervise(
        indexer: object, source_dir: Path, marker: Path, **kwargs: object
    ) -> None:
        if isinstance(indexer, WikiIndexer):
            built.append(indexer.wiki_dir)

    async def no_aggregate(marker: Path, children: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("scout.sync_job._supervise", fake_supervise)
    monkeypatch.setattr("scout.sync_job._aggregate_readiness", no_aggregate)
    wiki = tmp_path / "replica" / "current" / "wiki"
    await _async_main(
        FakeIndexer(),
        raw_dir=tmp_path / "raw",
        wiki_dir=wiki,
        wiki_watch_dir=tmp_path / "replica",
        readiness_path=tmp_path / "ready",
    )
    assert built == [wiki]


@pytest.mark.asyncio
async def test_aggregate_readiness_is_the_conjunction_of_its_watchers(
    tmp_path: Path,
) -> None:
    """A half-working sync must report unhealthy, not ready."""
    marker = tmp_path / "ready"
    raw = tmp_path / "ready.raw"
    wiki = tmp_path / "ready.wiki"
    raw.write_text("1", encoding="utf-8")  # raw ready, wiki not
    ticks = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            assert not marker.exists()  # not ready while wiki is missing
            wiki.write_text("1", encoding="utf-8")  # both ready on the next pass
        elif ticks >= 2:
            raise _StopSupervision

    with contextlib.suppress(_StopSupervision):
        await _aggregate_readiness(marker, [raw, wiki], sleep=fake_sleep)

    assert marker.exists()  # set only once BOTH children were ready


@pytest.mark.asyncio
async def test_async_main_watches_only_raw_when_wiki_dir_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WIKI_DIR unset must behave exactly as before this change."""
    supervised: list[Path] = []

    async def fake_supervise(
        indexer: object, source_dir: Path, marker: Path, **kwargs: object
    ) -> None:
        supervised.append(source_dir)

    async def no_aggregate(marker: Path, children: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("scout.sync_job._supervise", fake_supervise)
    monkeypatch.setattr("scout.sync_job._aggregate_readiness", no_aggregate)
    await _async_main(
        FakeIndexer(),
        raw_dir=tmp_path / "raw",
        wiki_dir=None,
        readiness_path=tmp_path / "ready",
    )
    assert supervised == [tmp_path / "raw"]


@pytest.mark.asyncio
async def test_wiki_indexer_reports_what_it_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cycle that skipped everything and one that rebuilt everything cost
    very different amounts; the status line is where that shows."""

    async def fake_ingest_wiki(
        wiki_dir: Path, **kwargs: object
    ) -> list[dict[str, object]]:
        return [
            {"source_uri": "a.md", "status": "ingested_ok"},
            {"source_uri": "b.md", "status": "unchanged"},
            {"source_uri": "c.md", "status": "unchanged"},
        ]

    async def fake_reconcile(wiki_dir: Path, **kwargs: object) -> list[str]:
        return []

    monkeypatch.setattr("scout.sync_job.ingest_wiki", fake_ingest_wiki)
    monkeypatch.setattr("scout.sync_job.reconcile_wiki_deletions", fake_reconcile)

    outcome = await WikiIndexer(wiki_dir=tmp_path).index()
    assert outcome.status == "1 indexed, 2 unchanged, 0 deleted"


@pytest.mark.asyncio
async def test_wiki_indexer_logs_correlated_structured_stage_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One published commit must be traceable through every hidden stage."""
    commit = "a" * 40
    snapshot = tmp_path / "snapshots" / commit
    (snapshot / "wiki").mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(snapshot, target_is_directory=True)

    async def fake_ingest_wiki(
        wiki_dir: Path, **kwargs: object
    ) -> list[dict[str, object]]:
        observer = kwargs["stage_observer"]
        assert callable(observer)
        for stage in (
            "chunk_complete",
            "embed_request_sent",
            "embed_response_received",
            "postgres_commit",
            "row_visible",
        ):
            observer(  # type: ignore[operator]
                stage,
                {"source_uri": "probe.md", "chunk_count": 1},
            )
        return [{"source_uri": "probe.md", "status": "ingested_ok"}]

    async def fake_reconcile(wiki_dir: Path, **kwargs: object) -> list[str]:
        return []

    monkeypatch.setattr("scout.sync_job.ingest_wiki", fake_ingest_wiki)
    monkeypatch.setattr("scout.sync_job.reconcile_wiki_deletions", fake_reconcile)

    outcome = await WikiIndexer(wiki_dir=current / "wiki").index()

    assert outcome.ok
    records = [
        json.loads(line.partition("[sync-job] ")[2])
        for line in capsys.readouterr().out.splitlines()
        if "[sync-job] " in line
    ]
    assert [record["stage"] for record in records] == [
        "watcher_wake",
        "chunk_complete",
        "embed_request_sent",
        "embed_response_received",
        "postgres_commit",
        "row_visible",
        "cycle_complete",
    ]
    assert all(record["event"] == "wiki_sync_stage" for record in records)
    assert all(record["correlation_id"] == commit for record in records)
    assert all(float(record["elapsed_ms"]) >= 0 for record in records)
    assert all(str(record["observed_at"]).endswith("+00:00") for record in records)


def test_wiki_stage_correlation_rejects_a_non_snapshot_path(tmp_path: Path) -> None:
    """A directory name must not be mislabeled as a commit correlation id."""
    wiki = tmp_path / "ordinary" / "wiki"
    wiki.mkdir(parents=True)
    assert _wiki_snapshot_commit(wiki) == "unversioned"


def test_structured_stage_identity_cannot_be_overridden(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Observer details are data and cannot replace the trusted envelope."""
    _emit_wiki_sync_stage(
        "b" * 40,
        "postgres_commit",
        cycle_started=0.0,
        details={
            "correlation_id": "forged",
            "event": "forged",
            "stage": "forged",
        },
    )
    record = json.loads(capsys.readouterr().out.partition("[sync-job] ")[2])
    assert record["correlation_id"] == "b" * 40
    assert record["event"] == "wiki_sync_stage"
    assert record["stage"] == "postgres_commit"


@pytest.mark.asyncio
async def test_a_permanent_fault_in_one_corpus_leaves_the_other_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The raw corpus can be unbuildable while the vault is perfectly fine.

    Measured on the first real deployment: the raw pipeline refused to start
    because this image cannot reproduce a PDF-derived corpus, and it took the
    vault watcher down with it -- nine container restarts, and W-2 never ran.
    A configuration fault in one corpus is not a reason to stop indexing the
    other.
    """
    marker = tmp_path / "ready"
    still_running = asyncio.Event()

    async def one_fails_one_runs(
        indexer: object, source_dir: Path, readiness: Path, **kwargs: object
    ) -> None:
        if source_dir.name == "raw":
            raise _PermanentSyncFailure(str(source_dir))
        readiness.write_text("ready\n", encoding="utf-8")
        still_running.set()
        await asyncio.Event().wait()  # a live watcher never returns

    monkeypatch.setattr("scout.sync_job._supervise", one_fails_one_runs)
    service = asyncio.ensure_future(
        _async_main(
            FakeIndexer(),
            raw_dir=tmp_path / "raw",
            wiki_dir=tmp_path / "wiki",
            readiness_path=marker,
        )
    )
    await asyncio.wait_for(still_running.wait(), timeout=5)
    # Long enough for a propagating exception to unwind gather, its finally
    # block and the cancellation of the siblings -- a single sleep(0) is not,
    # and would let this pass against the very behaviour it exists to catch.
    await asyncio.sleep(0.1)

    assert not service.done(), "the surviving watcher was cancelled with its sibling"
    service.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await service
    # Half a service is not a ready service.
    assert not marker.exists()


@pytest.mark.asyncio
async def test_the_process_exits_once_every_watcher_has_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Staying up with nothing left to watch would be a lie to the orchestrator."""

    async def all_fail(
        indexer: object, source_dir: Path, readiness: Path, **kwargs: object
    ) -> None:
        raise _PermanentSyncFailure(str(source_dir))

    monkeypatch.setattr("scout.sync_job._supervise", all_fail)
    with pytest.raises(SystemExit) as caught:
        await _async_main(
            FakeIndexer(),
            raw_dir=tmp_path / "raw",
            wiki_dir=tmp_path / "wiki",
            readiness_path=tmp_path / "ready",
        )
    assert caught.value.code == 1
