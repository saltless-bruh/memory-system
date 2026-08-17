"""sync-job — Nhịp A auto-ingest: a `raw/` change triggers a RAG reindex.

Serves T-3.2 / R-6.1 (design.md §5, "Nhịp A"). The rule of the two ingest
cadences:

  * **Nhịp A (this module):** a member drops a file into ``raw/``; the system
    indexes it into RAG **automatically**, no manual step. Dropping ten files
    does *not* mint ten wiki pages — that is the deliberately-manual Nhịp B
    (compile-on-demand, ``scripts/propose_page.py`` + ``verify_addresses.py``).

The design draws the trigger as ``webhook raw/ commit -> RAG index ->
gen_index.py``. This module is engine-agnostic about *how* the change is
detected: it consumes a stream of change batches (a filesystem watch in
production, or a git post-receive webhook firing one batch) and, per batch,
calls the rag service's ``/index`` (which re-scans ``raw/`` only, R-3.2). An
optional ``regen`` hook re-runs the deterministic index generator afterwards
so a commit that also touched ``wiki/`` keeps ``wiki/index.md`` current — it
is a no-op when nothing wiki-side changed (``gen_index.py`` is idempotent).

The reindex trigger (`sync_once`) and the loop (`watch`) are the testable
core; only the concrete watchfiles/CLI wiring is untested (`# pragma`).
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class IndexOutcome:
    """Result of one reindex trigger.

    Attributes:
        ok: True when the rag service reported a successful index.
        status: The service's raw ``status`` string (``"indexed"`` on success)
            — carried through for logging/diagnostics.
    """

    ok: bool
    status: str


@runtime_checkable
class RagIndexer(Protocol):
    """Anything that can (re)index ``raw/`` into the RAG store.

    Kept as a Protocol so the sync loop is testable with a fake and so the
    concrete engine (RAG-Anything today, a V2 engine later) stays swappable —
    the same seam as `scout.types.RagBackend` (R-4.8).
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
    """`RagIndexer` that calls the internal rag service's ``/index`` over HTTP.

    Attributes:
        base_url: The rag service base URL (``http://rag:8000`` on the compose
            network — internal only, no published port; R-4.2).
        timeout: Per-request timeout in seconds. Indexing parses documents with
            MinerU and can be slow, so this is generous by default.
    """

    base_url: str = "http://rag:8000"
    timeout: float = 3600.0

    async def index(self) -> IndexOutcome:
        """POST ``/index`` off the event loop and classify the response."""
        return await asyncio.to_thread(self._index_sync)

    def _index_sync(self) -> IndexOutcome:
        req = urllib.request.Request(
            f"{self.base_url}/index",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            payload = json.load(resp)
        status = str(payload.get("status", ""))
        return IndexOutcome(ok=status == "indexed", status=status)


@dataclass(slots=True)
class PgVectorDirectIndexer:
    """`RagIndexer` that directly parses and ingests `raw_dir` into PostgreSQL 16."""

    raw_dir: Path = Path("raw")
    allowed_depts: tuple[str, ...] = ("all",)

    async def index(self) -> IndexOutcome:
        """Runs the direct V2 ingestion pipeline on raw_dir."""
        from scout.ingest import ingest_directory

        try:
            results = await ingest_directory(
                dir_path=self.raw_dir,
                allowed_depts=list(self.allowed_depts),
                dry_run=False,
            )
            count = len(results)
            return IndexOutcome(ok=True, status=f"ingested_{count}_files")
        except Exception as e:
            return IndexOutcome(ok=False, status=f"error: {e}")


async def sync_once(indexer: RagIndexer, *, regen: Regen | None = None) -> IndexOutcome:
    """Run one reindex, then the optional post-index hook.

    Args:
        indexer: The RAG indexer to trigger.
        regen: Optional hook (e.g. `gen_index`) run only after a *successful*
            index — a failed index must not stamp a fresh index.md over a
            corpus that did not actually update.

    Returns:
        The `IndexOutcome` from the indexer.
    """
    outcome = await indexer.index()
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
    async for _batch in changes:
        await sync_once(indexer, regen=regen)
        handled += 1
    return handled


def _awatch_raw(  # pragma: no cover - thin watchfiles adapter
    raw_dir: Path, stop: object | None
) -> AsyncIterator[object]:
    """Yield change batches for `raw_dir` via watchfiles (debounced)."""
    from watchfiles import awatch

    return awatch(raw_dir, stop_event=stop)


def main() -> int:  # pragma: no cover - process entry point
    """Watch ``$RAW_DIR`` and reindex into PostgreSQL on every change."""
    raw_dir = Path(os.environ.get("RAW_DIR", "/data/raw"))
    if "POSTGRES_HOST" in os.environ or os.environ.get("RAG_BACKEND", "pgvector") == "pgvector":
        indexer: RagIndexer = PgVectorDirectIndexer(raw_dir=raw_dir)
        print(f"[sync-job] watching {raw_dir} -> PostgreSQL pgvector (Nhịp A)")
    else:
        indexer = HttpRagIndexer(base_url=os.environ.get("RAG_URL", "http://rag:8000"))
        print(f"[sync-job] watching {raw_dir} -> {indexer.base_url}/index (Nhịp A)")
    asyncio.run(watch(indexer, raw_dir=raw_dir))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
