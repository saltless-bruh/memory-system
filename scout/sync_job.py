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
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import asyncpg
import httpx

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
    """Raised after a sync attempt cannot produce a valid corpus."""


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

    def acl_file(self) -> Path:
        """Return the ACL map governing `raw_dir`."""
        from scout.ingest import DEFAULT_ACL_FILENAME

        if self.acl_path is not None:
            return self.acl_path
        return self.raw_dir / DEFAULT_ACL_FILENAME

    async def index(self) -> IndexOutcome:
        """Runs the direct V2 ingestion pipeline on raw_dir under its ACL map."""
        from scout.ingest import AclPolicyError, DocumentAclMap, ingest_directory

        acl_file = self.acl_file()
        try:
            acl = DocumentAclMap.from_file(acl_file, base_dir=self.raw_dir)
        except AclPolicyError as exc:
            # Never retried and never defaulted: without a readable policy the
            # ingest role has no authority to publish anything.
            print(f"[sync-job] FATAL: {exc}", file=sys.stderr)
            return IndexOutcome(ok=False, status="error:AclPolicyError")
        try:
            results = await ingest_directory(
                dir_path=self.raw_dir,
                acl=acl,
                dry_run=False,
                embedder=self.embedder,
            )
            count = len(results)
            return IndexOutcome(ok=True, status=f"ingested_{count}_files")
        except Exception as exc:
            return IndexOutcome(
                ok=False,
                status=f"error:{type(exc).__name__}",
                retryable=_is_transient(exc),
            )


def _is_transient(exc: BaseException) -> bool:
    """Classify only transport/database connectivity failures for retry."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, urllib.error.HTTPError):
            return 500 <= current.code < 600 or current.code in {408, 429}
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

    Returns:
        The number of change batches handled (useful for tests; a live watch
        runs until stopped).

    Raises:
        ValueError: If neither `changes` nor `raw_dir` is provided.
    """
    if changes is None:
        if raw_dir is None:
            raise ValueError("watch() needs either `changes` or `raw_dir`")
        changes = _awatch_raw(raw_dir, stop)

    handled = 0
    if initial_sync:
        outcome = await sync_once(indexer, regen=regen)
        if not outcome.ok:
            raise SyncFailure("initial synchronization failed")
        handled += 1

    async for _batch in changes:
        outcome = await sync_once(indexer, regen=regen)
        if not outcome.ok:
            raise SyncFailure("watched synchronization failed")
        handled += 1
    return handled


def _awatch_raw(  # pragma: no cover - thin watchfiles adapter
    raw_dir: Path, stop: object | None
) -> AsyncIterator[object]:
    """Yield change batches for `raw_dir` via watchfiles (debounced)."""
    from watchfiles import awatch

    return awatch(raw_dir, stop_event=stop)


def _set_readiness(path: Path, ready: bool) -> None:
    """Atomically publish or clear the sync-job readiness marker."""
    if not ready:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("ready\n", encoding="utf-8")
    temporary.replace(path)


async def _async_main(
    indexer: RagIndexer,
    raw_dir: Path,
    readiness_path: Path | None = None,
) -> None:
    marker = readiness_path or Path(
        os.environ.get("SYNC_READY_FILE", "/tmp/snp-sync-job/ready")
    )
    _set_readiness(marker, False)
    # Cold-start startup sync before entering watch loop
    outcome = await sync_once(indexer)
    if not outcome.ok:
        print(
            f"[sync-job] FATAL: Initial cold-start sync failed: {outcome.status}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    _set_readiness(marker, True)
    try:
        await watch(indexer, raw_dir=raw_dir, initial_sync=False)
    except SyncFailure as exc:
        _set_readiness(marker, False)
        print("[sync-job] FATAL: watched sync failed", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> int:  # pragma: no cover - process entry point
    """Watch ``$RAW_DIR`` and reindex into PostgreSQL on every change."""
    raw_dir = Path(os.environ.get("RAW_DIR", "/data/raw"))
    configured_acl = os.environ.get("RAW_ACL_FILE", "").strip()
    if "POSTGRES_HOST" in os.environ or os.environ.get("RAG_BACKEND", "pgvector") == "pgvector":
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
    asyncio.run(_async_main(indexer, raw_dir))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
