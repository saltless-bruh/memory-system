"""sync-job — Nhịp A auto-ingest: a `raw/` change triggers a RAG reindex.

Serves T-3.2 / R-6.1 (design.md §5, "Nhịp A"). The rule of the two ingest
cadences:

  * **Nhịp A (this module):** a member drops a file into ``raw/``; the system
    indexes it into RAG **automatically**, no manual step. Dropping ten files
    does *not* mint ten wiki pages — that is the deliberately-manual Nhịp B
    (compile-on-demand, ``scripts/propose_page.py`` + ``verify_addresses.py``).

This module is engine-agnostic about *how* a change is detected: it consumes a
stream of change batches and, per batch, invokes an injected indexer. The
production indexer parses and writes ``raw/`` directly to PostgreSQL through
the dedicated ingestion role. An optional ``regen`` hook re-runs the
deterministic wiki index generator afterwards.

The reindex trigger (`sync_once`) and the loop (`watch`) are the testable
core; only the concrete watchfiles/CLI wiring is untested (`# pragma`).
"""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.error
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import asyncpg
import httpx

from scout.wiki_ingest import ingest_wiki, reconcile_wiki_deletions

if TYPE_CHECKING:
    from scout.chunker import LiteLLMBatchEmbedder


@dataclass(frozen=True, slots=True)
class IndexOutcome:
    """Result of one reindex trigger.

    Attributes:
        ok: True when the indexer reported a successful update.
        status: The implementation's bounded status string, carried through
            for logging and diagnostics.
    """

    ok: bool
    status: str
    retryable: bool = False


class SyncFailure(RuntimeError):
    """Raised after a sync attempt cannot produce a valid corpus.

    Carries the failed outcome's `retryable` flag across the raise, because the
    caller has to distinguish a dependency that is down (wait for it) from a
    corpus or policy that is wrong (stop). Defaults to False: a failure of
    unknown provenance is treated as permanent, so an unclassified fault stops
    loudly instead of retrying forever.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@runtime_checkable
class RagIndexer(Protocol):
    """Anything that can (re)index ``raw/`` into the RAG store.

    Kept as a Protocol so the sync loop is testable with a fake and so the
    concrete implementation stays swappable at the same seam as
    `scout.types.RagBackend` (R-4.8).
    """

    async def index(self) -> IndexOutcome:
        """Trigger a full reindex of ``raw/`` and report the outcome."""
        ...


# A post-index hook, e.g. regenerating wiki/index.md. Sync or async: a sync
# callable does its work and returns None (nothing to await); an async one
# returns the awaitable to drive. Injected so unit tests never shell out.
Regen = Callable[[], Awaitable[None] | None]


@dataclass(slots=True)
class HttpRagIndexer:
    """Optional `RagIndexer` adapter for an explicitly configured HTTP API.

    Attributes:
        base_url: The explicitly configured internal indexing endpoint.
        timeout: Per-request timeout in seconds.
    """

    base_url: str = "http://rag:8000"
    timeout: float = 3600.0
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    async def index(self) -> IndexOutcome:
        """POST ``/index`` with an explicitly closed asynchronous client."""
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = await client.post("/index", json={})
                response.raise_for_status()
                payload = response.json()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise SyncFailure("RAG index transport failed") from exc
        if not isinstance(payload, dict):
            raise SyncFailure("RAG index returned a malformed response")
        status = str(payload.get("status", ""))
        return IndexOutcome(ok=status == "indexed", status=status)


@dataclass(slots=True)
class PgVectorDirectIndexer:
    """`RagIndexer` that directly parses and ingests `raw_dir` into PostgreSQL 16.

    The indexer carries **no** department of its own. A checked-in ACL map next
    to the corpus decides each document's `allowed_depts`, and a file it does
    not match is not indexed at all — an ingest that cannot read its policy
    publishes nothing rather than publishing everything as `all`.

    Attributes:
        raw_dir: The corpus root to index.
        acl_path: The document ACL map. Defaults to the map checked in at the
            corpus root, which is how `docker compose` ships it.
        embedder: Optional injected embedder; the pipeline builds one otherwise.
    """

    raw_dir: Path = Path("raw")
    acl_path: Path | None = None
    embedder: LiteLLMBatchEmbedder | None = None
    #: Parsed documents reused across the retries of one cycle. The failure that
    #: actually occurs is at the embed step, and re-parsing the whole corpus to
    #: reach it again costs work that scales with the corpus rather than with the
    #: failure. Cleared on success so it never outlives its cycle.
    parse_cache: Any = None

    def acl_file(self) -> Path:
        """Return the ACL map governing `raw_dir`."""
        from scout.ingest import DEFAULT_ACL_FILENAME

        if self.acl_path is not None:
            return self.acl_path
        return self.raw_dir / DEFAULT_ACL_FILENAME

    async def index(self) -> IndexOutcome:
        """Runs the direct V2 ingestion pipeline on raw_dir under its ACL map."""
        from scout.ingest import (
            AclPolicyError,
            CapabilityMismatchError,
            DocumentAclMap,
            ingest_directory,
        )

        acl_file = self.acl_file()
        try:
            acl = DocumentAclMap.from_file(acl_file, base_dir=self.raw_dir)
        except AclPolicyError as exc:
            # Never retried and never defaulted: without a readable policy the
            # ingest role has no authority to publish anything.
            print(f"[sync-job] FATAL: {exc}", file=sys.stderr)
            return IndexOutcome(ok=False, status="error:AclPolicyError")
        if self.parse_cache is None:
            from scout.ingest import ParseCache

            self.parse_cache = ParseCache()
        try:
            results = await ingest_directory(
                dir_path=self.raw_dir,
                acl=acl,
                dry_run=False,
                embedder=self.embedder,
                parse_cache=self.parse_cache,
            )
            count = len(results)
            # The cycle finished. Holding a corpus-worth of parsed text past
            # here would save work nobody is going to repeat.
            self.parse_cache.clear()
            return IndexOutcome(ok=True, status=f"ingested_{count}_files")
        except CapabilityMismatchError as exc:
            # Permanent, and never retried: retrying changes nothing because the
            # difference is this process's own environment. Nothing was written
            # and nothing was deleted — reconciliation must not run, or rows are
            # purged for a corpus this process has just said it cannot rebuild.
            print(f"[sync-job] FATAL: {exc}", file=sys.stderr)
            return IndexOutcome(
                ok=False, status="error:CapabilityMismatchError", retryable=False
            )
        except Exception as exc:
            return IndexOutcome(
                ok=False,
                status=f"error:{type(exc).__name__}",
                retryable=_is_transient(exc),
            )


@dataclass(slots=True)
class WikiIndexer:
    """`RagIndexer` that ingests the knowledge vault into PostgreSQL.

    Unlike `PgVectorDirectIndexer` there is no ACL map: vault pages carry all
    four canonical departments (`WIKI_ALLOWED_DEPARTMENTS`), because the corpus
    has no editorial `department:` field to derive one from and inventing one
    would be fabricated metadata.

    Ingest runs before reconciliation, and reconciliation is skipped entirely
    when ingest fails. Either reversal empties the served corpus: reconciling
    first deletes rows for pages this cycle is about to re-add, and reconciling
    after a failure deletes rows for a corpus this process has just said it
    could not rebuild.

    Attributes:
        wiki_dir: The vault root to index.
        embedder: Optional injected embedder; `ingest_wiki` builds one otherwise.
    """

    wiki_dir: Path = Path("wiki")
    embedder: LiteLLMBatchEmbedder | None = None

    async def index(self) -> IndexOutcome:
        """Ingest every page, then purge rows whose file is gone."""
        try:
            results = await ingest_wiki(self.wiki_dir, embedder=self.embedder)
            deleted = await reconcile_wiki_deletions(self.wiki_dir)
        except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
            # A vault that is absent or unreadable is a configuration fault.
            # Waiting cannot fix it, so it must not be retried forever.
            return IndexOutcome(
                ok=False, status=f"vault unreadable: {exc}", retryable=False
            )
        except Exception as exc:  # noqa: BLE001 - classified, then re-reported
            return IndexOutcome(
                ok=False,
                status=f"vault ingest failed: {type(exc).__name__}: {exc}",
                retryable=_is_transient(exc),
            )
        # "ingested_ok" is the status `ingest_document` returns on a successful
        # upsert. Counting any other token here reports zero for every cycle.
        indexed = sum(1 for r in results if r.get("status") == "ingested_ok")
        return IndexOutcome(
            ok=True,
            status=f"{indexed} indexed, {len(deleted)} deleted",
        )


def _is_transient(exc: BaseException) -> bool:
    """Classify only transport/database connectivity failures for retry."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, urllib.error.HTTPError):
            return 500 <= current.code < 600 or current.code in {408, 429}
        # The async embed path (`aembed_texts`) raises httpx errors, not urllib
        # ones. Without this branch a 500 from that path is called permanent
        # while the identical 500 from the sync path is called transient.
        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            return 500 <= status < 600 or status in {408, 429}
        if isinstance(current, urllib.error.URLError):
            return True
        if isinstance(
            current,
            (
                asyncpg.PostgresConnectionError,
                httpx.NetworkError,
                httpx.TimeoutException,
                TimeoutError,
                ConnectionError,
            ),
        ):
            return True
        current = current.__cause__
    return False


async def sync_once(
    indexer: RagIndexer,
    *,
    regen: Regen | None = None,
    max_attempts: int = 3,
    base_delay: float = 0.25,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> IndexOutcome:
    """Run one reindex, then the optional post-index hook.

    Args:
        indexer: The RAG indexer to trigger.
        regen: Optional hook (e.g. `gen_index`) run only after a *successful*
            index — a failed index must not stamp a fresh index.md over a
            corpus that did not actually update.

    Returns:
        The `IndexOutcome` from the indexer.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    outcome = IndexOutcome(ok=False, status="not_attempted")
    for attempt in range(max_attempts):
        outcome = await indexer.index()
        if outcome.ok or not outcome.retryable or attempt + 1 >= max_attempts:
            break
        await sleep(base_delay * (2**attempt))
    if regen is not None and outcome.ok:
        result = regen()
        if result is not None:  # async regen: drive its awaitable to completion
            await result
    return outcome


async def watch(
    indexer: RagIndexer,
    *,
    regen: Regen | None = None,
    changes: AsyncIterator[object] | None = None,
    raw_dir: Path | None = None,
    stop: object | None = None,
    initial_sync: bool = False,
    recursive: bool = True,
) -> int:
    """Reindex once per change batch until the stream ends (R-6.1).

    Args:
        indexer: The RAG indexer to trigger on every batch.
        regen: Optional post-index hook (see `sync_once`).
        changes: A stream of change batches. When omitted, a filesystem watch
            over `raw_dir` is used; tests inject a finite async iterator here.
        raw_dir: Directory to watch when `changes` is not supplied.
        stop: Optional stop event forwarded to the filesystem watcher so the
            loop can be shut down cleanly.
        initial_sync: When True, runs an initial sync before awaiting changes.
        recursive: Whether the filesystem watch descends into subdirectories.
            False is for a directory that holds a *publication pointer* rather
            than the corpus itself -- see `_supervise`.

    Returns:
        The number of change batches handled (useful for tests; a live watch
        runs until stopped).

    Raises:
        ValueError: If neither `changes` nor `raw_dir` is provided.
    """
    if changes is None:
        if raw_dir is None:
            raise ValueError("watch() needs either `changes` or `raw_dir`")
        changes = _awatch_raw(raw_dir, stop, recursive=recursive)

    handled = 0
    if initial_sync:
        outcome = await sync_once(indexer, regen=regen)
        if not outcome.ok:
            raise SyncFailure(
                "initial synchronization failed", retryable=outcome.retryable
            )
        handled += 1

    async for _batch in changes:
        outcome = await sync_once(indexer, regen=regen)
        if not outcome.ok:
            raise SyncFailure(
                "watched synchronization failed", retryable=outcome.retryable
            )
        handled += 1
    return handled


def _awatch_raw(  # pragma: no cover - thin watchfiles adapter
    raw_dir: Path, stop: object | None, *, recursive: bool = True
) -> AsyncIterator[object]:
    """Yield change batches for `raw_dir` via watchfiles (debounced)."""
    from watchfiles import awatch

    return awatch(raw_dir, stop_event=stop, recursive=recursive)


def _set_readiness(path: Path, ready: bool) -> None:
    """Atomically publish or clear the sync-job readiness marker."""
    if not ready:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("ready\n", encoding="utf-8")
    temporary.replace(path)


#: First wait after a transient failure, in seconds.
COLD_START_BASE_DELAY = 5.0
#: Ceiling on the wait between attempts, in seconds.
COLD_START_MAX_DELAY = 300.0


class _PermanentSyncFailure(Exception):
    """A fault no amount of waiting can fix; the process must exit.

    Raised instead of `SystemExit` because a supervisor runs inside a Task, and
    `SystemExit` raised in a Task is re-raised into the event loop rather than
    delivered to whoever is awaiting it -- it tears down the loop instead of the
    service. `_async_main` translates this into the `SystemExit(1)` the process
    entry point expects.
    """


async def _supervise(
    indexer: RagIndexer,
    source_dir: Path,
    readiness_path: Path,
    *,
    recursive: bool = True,
    base_delay: float = COLD_START_BASE_DELAY,
    max_delay: float = COLD_START_MAX_DELAY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Index this watcher's corpus, then watch `source_dir`, surviving an outage.

    `source_dir` is what is *watched*; the corpus that is *indexed* is whatever
    `indexer` was configured with. Those are the same directory for `raw/`, and
    deliberately different for the vault: `host-sync` publishes by materialising
    `snapshots/<commit>/` and atomically retargeting a `current` symlink, so an
    inotify watch on `current/wiki` is bound to the previous snapshot's inode
    and sees nothing at all through a republish (measured: 0 batches). The
    directory that holds the pointer does see it, which is why that directory is
    watched, and why it is watched with `recursive=False` -- recursing would
    re-trigger on every file of every snapshot as it materialises.

    Exiting on a transient failure looks like the disciplined thing to do --
    crash, let the orchestrator restart. It is not, here. Docker's restart
    backoff resets once a container survives 10 seconds, and this container
    always does (a DNS timeout alone takes longer), so the backoff never
    accumulates and the process simply respawns forever. On 2026-08-24 that
    produced 238 restarts, each re-reading the corpus and re-attempting paid
    embedding calls, while the health check reported `starting` throughout
    because every restart reset its start period.

    So the wait lives here instead. While a retryable failure persists the
    watcher stays up with its readiness marker **cleared**, which is what lets
    the health check say `unhealthy` -- alive and honestly reporting failure,
    rather than absent. A permanent failure (a corpus whose ACL policy cannot be
    read, a vault directory that does not exist) still exits immediately:
    waiting cannot fix a configuration fault, and a crash-looping container is
    the signal an operator needs to see.

    The attempt counter is never reset within a process lifetime. Resetting it
    on a successful cycle is exactly the mistake Docker makes, and it is how a
    slow failure loop reappears; the cost is that a later, unrelated blip waits
    at the ceiling rather than at `base_delay`.
    """
    _set_readiness(readiness_path, False)
    attempt = 0

    async def back_off(reason: str) -> None:
        nonlocal attempt
        delay = min(max_delay, base_delay * (2**attempt))
        attempt += 1
        print(
            f"[sync-job] {source_dir}: {reason}; dependency looks transient, "
            f"retrying in {delay:.0f}s (attempt {attempt}, readiness cleared)",
            file=sys.stderr,
        )
        await sleep(delay)

    while True:
        outcome = await sync_once(indexer)
        if not outcome.ok:
            if not outcome.retryable:
                print(
                    f"[sync-job] FATAL: {source_dir}: cold-start sync failed: "
                    f"{outcome.status}",
                    file=sys.stderr,
                )
                raise _PermanentSyncFailure(str(source_dir))
            await back_off(f"cold-start sync failed: {outcome.status}")
            continue

        _set_readiness(readiness_path, True)
        try:
            await watch(
                indexer,
                raw_dir=source_dir,
                initial_sync=False,
                recursive=recursive,
            )
        except SyncFailure as exc:
            _set_readiness(readiness_path, False)
            if not exc.retryable:
                print(
                    f"[sync-job] FATAL: {source_dir}: watched sync failed",
                    file=sys.stderr,
                )
                raise _PermanentSyncFailure(str(source_dir)) from exc
            await back_off(f"watched sync failed: {exc}")
            continue
        return


async def _aggregate_readiness(
    marker: Path,
    children: Sequence[Path],
    *,
    interval: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Hold the service-level marker at the conjunction of its watchers.

    The health check reads one path. A half-working sync -- vault indexing, raw
    stalled -- must report unhealthy, so the marker is set only while *every*
    configured watcher is ready, and cleared the moment one is not.

    This cannot be done after `asyncio.gather` returns: in a live run no
    supervisor ever returns, because `watch()` runs until the process stops.
    """
    while True:
        _set_readiness(marker, all(child.exists() for child in children))
        await sleep(interval)


async def _async_main(
    indexer: RagIndexer,
    raw_dir: Path,
    readiness_path: Path | None = None,
    *,
    wiki_dir: Path | None = None,
    wiki_watch_dir: Path | None = None,
    wiki_indexer: RagIndexer | None = None,
    base_delay: float = COLD_START_BASE_DELAY,
    max_delay: float = COLD_START_MAX_DELAY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Supervise the raw watcher, and the vault watcher when one is configured.

    The two corpora fail independently. A vault page that cannot be embedded
    must not stall raw ingest, which the compile pipeline reads, and a raw
    outage must not stop the vault reaching the index -- that second direction
    is W-2, the loop the whole retrieval inversion rests on.

    `wiki_watch_dir` names the directory whose changes *signal* a new vault
    publication, when that is not the vault directory itself. Setting it selects
    a non-recursive watch, because such a directory holds a pointer rather than
    a corpus.

    Readiness is the conjunction: the service is ready only when every
    configured watcher has completed a cycle, so a half-working sync reports
    unhealthy rather than ready.
    """
    marker = readiness_path or Path(
        os.environ.get("SYNC_READY_FILE", "/tmp/snp-sync-job/ready")
    )
    # The service is not ready until a watcher says so, and this must not wait
    # on the aggregator's first tick: a permanent cold-start failure exits
    # before that tick would ever run.
    _set_readiness(marker, False)

    markers = [marker.with_name(f"{marker.name}.raw")]
    supervisors = [
        _supervise(
            indexer,
            raw_dir,
            markers[0],
            base_delay=base_delay,
            max_delay=max_delay,
            sleep=sleep,
        )
    ]
    if wiki_dir is not None:
        markers.append(marker.with_name(f"{marker.name}.wiki"))
        supervisors.append(
            _supervise(
                wiki_indexer or WikiIndexer(wiki_dir=wiki_dir),
                wiki_watch_dir or wiki_dir,
                markers[1],
                recursive=wiki_watch_dir is None,
                base_delay=base_delay,
                max_delay=max_delay,
                sleep=sleep,
            )
        )
    watchers = [asyncio.ensure_future(coroutine) for coroutine in supervisors]
    # The aggregator keeps its own clock. `sleep` is the *backoff* clock a
    # caller injects to script retry timing, and feeding a polling loop from it
    # would both distort those timings and never terminate.
    aggregator = asyncio.ensure_future(_aggregate_readiness(marker, markers))
    try:
        await asyncio.gather(*watchers)
    except _PermanentSyncFailure as exc:
        raise SystemExit(1) from exc
    finally:
        # In a live run no watcher ever returns, so this only runs on shutdown
        # or on a permanent failure -- where the siblings must not be left
        # running against a service that is on its way out.
        for pending in (*watchers, aggregator):
            pending.cancel()
        await asyncio.gather(*watchers, aggregator, return_exceptions=True)
        # The aggregator polls, so its last observation may predate the
        # watchers' final state. Recompute once here so the marker a health
        # check reads is never left describing a moment that has passed.
        _set_readiness(marker, all(child.exists() for child in markers))


def main() -> int:  # pragma: no cover - process entry point
    """Watch ``$RAW_DIR`` and reindex into PostgreSQL on every change."""
    raw_dir = Path(os.environ.get("RAW_DIR", "/data/raw"))
    configured_acl = os.environ.get("RAW_ACL_FILE", "").strip()
    if (
        "POSTGRES_HOST" in os.environ
        or os.environ.get("RAG_BACKEND", "pgvector") == "pgvector"
    ):
        direct = PgVectorDirectIndexer(
            raw_dir=raw_dir,
            acl_path=Path(configured_acl) if configured_acl else None,
        )
        indexer: RagIndexer = direct
        print(
            f"[sync-job] watching {raw_dir} -> PostgreSQL pgvector (Nhịp A), "
            f"document ACLs from {direct.acl_file()}"
        )
    else:
        indexer = HttpRagIndexer(base_url=os.environ.get("RAG_URL", "http://rag:8000"))
        print(f"[sync-job] watching {raw_dir} -> {indexer.base_url}/index (Nhịp A)")
    configured_wiki = os.environ.get("WIKI_DIR", "").strip()
    wiki_dir = Path(configured_wiki) if configured_wiki else None
    configured_watch = os.environ.get("WIKI_WATCH_DIR", "").strip()
    wiki_watch_dir = Path(configured_watch) if configured_watch else None
    if wiki_dir is not None:
        watched = wiki_watch_dir or wiki_dir
        print(
            f"[sync-job] also watching {watched} -> PostgreSQL pgvector "
            f"(vault at {wiki_dir})"
        )
    asyncio.run(
        _async_main(indexer, raw_dir, wiki_dir=wiki_dir, wiki_watch_dir=wiki_watch_dir)
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
