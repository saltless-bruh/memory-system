# Engine Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two engine claims — the agent queries the index and gets back an address and a file (W-1), and a change in the vault reaches the index (W-2) — and prove both with re-runnable behavioral measurements.

**Architecture:** No new subsystem. `ingest_wiki()` and `reconcile_deletions()` are already written; `sync_job`'s `RagIndexer` Protocol, retry discipline and watcher are already parameterised. The work is a `WikiIndexer` at the existing seam, a second watcher beside the existing one, deployment of a service that is declared but has no container, one startup guard, and three oracles that measure behavior instead of shape.

**Tech Stack:** Python ≥3.12, asyncpg, pgvector, cyclopts, FastMCP, watchfiles, pytest (`--disable-socket` offline), ruff (line-length 88), mypy `strict`, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-04-engine-completion-design.md`

## Global Constraints

- Python ≥ 3.12. Type hints everywhere; `mypy scout scripts` must pass in strict mode.
- `ruff check .` and `ruff format --check .` must pass. Line length 88.
- The offline suite runs socket-free: `env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest -m 'not integration' --disable-socket -q`. It is currently green at **1387 passed, 29 deselected**. It must stay green.
- **No placeholders.** A component is working through its real production path or it is deleted. No `TODO`, no cosmetic surface, no function that nothing calls.
- Retrieval floors are `recall@1 ≥ 0.60`, `recall@5 ≥ 0.85`. Currently measured 0.80 and 1.00. **Floors never move to accommodate a result.**
- Every gate that asserts an absence must first be shown to detect a planted positive. An oracle that cannot fail is not evidence.
- `.agent/` ≡ `.claude/` ≡ `packages/snp-agent/` byte-for-byte for shared subtrees.
- Never push the source repository to a remote. Vault-repo `main` pushes are covered by a recorded owner exception and are used only by Task 7.
- Canonical departments: `redteam`, `blueteam`, `ai_eng`, `infra`. Wiki documents carry all four (`WIKI_ALLOWED_DEPARTMENTS`).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `scout/sync_job.py` | Add `WikiIndexer`; extend `_async_main`/`main` to supervise two watchers with failure isolation | 1, 2 |
| `tests/test_sync_job.py` | Unit coverage for both, offline, with fakes | 1, 2 |
| `scout/cli/commands/wiki.py` | Add `ingest_wiki` command — the shipped operator entry point | 3 |
| `scout/cli/declarations.py` | Register `ingest-wiki` in the command manifest | 3 |
| `tests/test_cli_wiki.py` | CLI contract for the new command | 3 |
| `docker-compose.yml` | Give `sync-job` the vault mount and `WIKI_DIR`; it is declared but has never had a container | 4 |
| `scout/serve.py` | Startup single-model guard | 5 |
| `tests/test_serve.py` | Guard unit coverage | 5 |
| `artifacts/v3/checks/engine_acceptance.py` | **New.** The three behavioral oracles: `find-read-cite`, `vault-change-propagates`, `live-sql` | 6, 7, 8 |
| `artifacts/v3/retrieval_questions.json` | Grow the question set; add the absent-page control | 6 |
| `.unlazy/v3-retrieval/gates/engine-2026-09-04.md` | **New.** The engine acceptance ledger | 6, 7, 8 |

---

### Task 1: `WikiIndexer` at the existing `RagIndexer` seam

Closes **E1** (`ingest_wiki` is complete and nothing calls it).

**Files:**
- Modify: `scout/sync_job.py` (add after `PgVectorDirectIndexer`, which ends before `_is_transient` at `:205`)
- Test: `tests/test_sync_job.py`

**Interfaces:**
- Consumes: `IndexOutcome(ok: bool, status: str, retryable: bool = False)` at `sync_job.py:40`; `RagIndexer` Protocol at `:70`; `SyncFailure(message, *, retryable)` at `:54`; `ingest_wiki(wiki_dir, *, conn, embedder, dry_run, env) -> list[dict[str, object]]` at `wiki_ingest.py:401`; `reconcile_deletions(dir_path, conn, dry_run, acl) -> list[str]` at `ingest.py:424`.
- Produces: `WikiIndexer(wiki_dir: Path, embedder: LiteLLMBatchEmbedder | None = None)` with `async def index(self) -> IndexOutcome`. Task 2 constructs it; Task 3 shares its ingest call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sync_job.py`:

```python
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
        return [
            {
                "source_uri": "a.md",
                "title": "A",
                "chunks_count": 3,
                "status": "indexed",
            },
            {
                "source_uri": "b.md",
                "title": "B",
                "chunks_count": 0,
                "status": "skipped_no_body",
            },
        ]

    async def fake_reconcile(dir_path: Path, **kwargs: object) -> list[str]:
        calls.append("reconcile")
        return ["gone.md"]

    monkeypatch.setattr("scout.sync_job.ingest_wiki", fake_ingest_wiki)
    monkeypatch.setattr("scout.sync_job.reconcile_deletions", fake_reconcile)

    outcome = await WikiIndexer(wiki_dir=tmp_path).index()

    assert outcome.ok is True
    # Ingest must precede reconciliation: reconciling first would delete rows
    # for pages this very cycle is about to re-add.
    assert calls == ["ingest", "reconcile"]
    assert "1 indexed" in outcome.status
    assert "1 deleted" in outcome.status


def test_wiki_indexer_is_a_rag_indexer() -> None:
    assert isinstance(WikiIndexer(wiki_dir=Path("wiki")), RagIndexer)


@pytest.mark.asyncio
async def test_wiki_indexer_reports_a_transient_fault_as_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dependency outage must be retryable, not fatal (the 238-restart lesson)."""

    async def boom(wiki_dir: Path, **kwargs: object) -> list[dict[str, object]]:
        raise OSError("connection refused")

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
```

Add to that file's imports: `from scout.sync_job import WikiIndexer` alongside the existing `sync_job` imports.

- [ ] **Step 2: Run tests to verify they fail**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_sync_job.py -k wiki_indexer --disable-socket -q
```

Expected: FAIL — `ImportError: cannot import name 'WikiIndexer'`.

- [ ] **Step 3: Implement `WikiIndexer`**

In `scout/sync_job.py`, add to the module imports:

```python
from scout.ingest import reconcile_deletions
from scout.wiki_ingest import ingest_wiki
```

Insert after `PgVectorDirectIndexer`:

```python
@dataclass(slots=True)
class WikiIndexer:
    """`RagIndexer` that ingests the knowledge vault into PostgreSQL.

    Unlike `PgVectorDirectIndexer` there is no ACL map: vault pages carry all
    four canonical departments (`WIKI_ALLOWED_DEPARTMENTS`), because the
    corpus has no editorial `department:` field to derive one from and
    inventing one would be fabricated metadata.

    Ingest runs before reconciliation. Reconciling first would delete rows for
    pages this cycle is about to re-add, briefly emptying the served corpus.

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
            deleted = await reconcile_deletions(self.wiki_dir)
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
        indexed = sum(1 for r in results if r.get("status") == "indexed")
        return IndexOutcome(
            ok=True,
            status=f"{indexed} indexed, {len(deleted)} deleted",
        )
```

`_is_transient` is defined at `:205`; `WikiIndexer` is declared after it in file order but only calls it at runtime, so no forward-reference problem arises. If ruff's import ordering objects, move the two new imports into the existing `TYPE_CHECKING`-adjacent block that already imports `LiteLLMBatchEmbedder`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_sync_job.py -k wiki_indexer --disable-socket -q
```

Expected: 4 passed.

- [ ] **Step 5: Static checks**

```bash
uv run ruff check scout/sync_job.py tests/test_sync_job.py && \
uv run ruff format --check scout/sync_job.py tests/test_sync_job.py && \
uv run mypy scout scripts
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add scout/sync_job.py tests/test_sync_job.py
git commit -m "feat(sync): index the vault through the existing RagIndexer seam

ingest_wiki() has been complete since leaf-1.2.1 and nothing called it. The
431 documents in the index were placed by hand. WikiIndexer connects it to
the sync loop at the seam PgVectorDirectIndexer already uses.

Ingest precedes reconciliation deliberately: the reverse order would delete
rows for pages the same cycle re-adds.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Task 2: Two watchers with failure isolation

Closes **E3** (no vault watcher — W-2 is open).

**Files:**
- Modify: `scout/sync_job.py` — `_async_main` at `:346`, `main` at `:419`
- Test: `tests/test_sync_job.py`

**Interfaces:**
- Consumes: `WikiIndexer` from Task 1; `watch(indexer, *, regen, changes, raw_dir, stop, initial_sync) -> int` at `:268`; `sync_once` at `:234`; `_set_readiness(path, ready)` at `:329`.
- Produces: `_supervise(indexer, source_dir, marker, *, base_delay, max_delay, sleep) -> None` — one watcher's full lifecycle. `_async_main` gains `wiki_dir: Path | None = None`. Task 4 sets `WIKI_DIR` in compose.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_one_watcher_failing_does_not_stop_the_other(
    tmp_path: Path,
) -> None:
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

    with contextlib.suppress(_StopSupervision):
        await asyncio.gather(
            _supervise(AlwaysFails(), tmp_path, wiki_marker, sleep=fake_sleep),
            _supervise(healthy, tmp_path, raw_marker, sleep=fake_sleep),
            return_exceptions=False,
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

    monkeypatch.setattr("scout.sync_job._supervise", fake_supervise)
    await _async_main(
        FakeIndexer(),
        raw_dir=tmp_path / "raw",
        wiki_dir=None,
        readiness_path=tmp_path / "ready",
    )
    assert supervised == [tmp_path / "raw"]
```

Add at the top of the test file if not present: `import asyncio`, `import contextlib`,
`from scout.sync_job import _aggregate_readiness, _supervise`, and

```python
class _StopSupervision(Exception):
    """Ends a supervision loop inside a test without killing the process."""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_sync_job.py -k "watcher or supervises or only_raw" --disable-socket -q
```

Expected: FAIL — `cannot import name '_supervise'`.

- [ ] **Step 3: Extract `_supervise` and rewrite `_async_main`**

Replace the body of `_async_main` (`:346`–`:417`) with the extracted supervisor plus a gather. The docstring explaining the 238-restart incident moves onto `_supervise` unchanged — it documents the backoff, which is what moved.

```python
async def _supervise(
    indexer: RagIndexer,
    source_dir: Path,
    readiness_path: Path,
    *,
    base_delay: float = COLD_START_BASE_DELAY,
    max_delay: float = COLD_START_MAX_DELAY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Index `source_dir`, then watch it, surviving a dependency outage.

    Exiting on a transient failure looks like the disciplined thing to do —
    crash, let the orchestrator restart. It is not, here. Docker's restart
    backoff resets once a container survives 10 seconds, and this container
    always does (a DNS timeout alone takes longer), so the backoff never
    accumulates and the process simply respawns forever. On 2026-08-24 that
    produced 238 restarts, each re-reading the corpus and re-attempting paid
    embedding calls, while the health check reported `starting` throughout
    because every restart reset its start period.

    So the wait lives here instead. While a retryable failure persists the
    watcher stays up with its readiness marker **cleared**, which is what lets
    the health check say `unhealthy` — alive and honestly reporting failure,
    rather than absent. A permanent failure (a corpus whose ACL policy cannot
    be read) still exits immediately: waiting cannot fix a configuration fault.

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
                raise SystemExit(1)
            await back_off(f"cold-start sync failed: {outcome.status}")
            continue

        _set_readiness(readiness_path, True)
        try:
            await watch(indexer, raw_dir=source_dir, initial_sync=False)
        except SyncFailure as exc:
            _set_readiness(readiness_path, False)
            if not exc.retryable:
                print(
                    f"[sync-job] FATAL: {source_dir}: watched sync failed",
                    file=sys.stderr,
                )
                raise SystemExit(1) from exc
            await back_off(f"watched sync failed: {exc}")
            continue
        return


async def _async_main(
    indexer: RagIndexer,
    raw_dir: Path,
    readiness_path: Path | None = None,
    *,
    wiki_dir: Path | None = None,
    wiki_indexer: RagIndexer | None = None,
    base_delay: float = COLD_START_BASE_DELAY,
    max_delay: float = COLD_START_MAX_DELAY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Supervise the raw watcher, and the vault watcher when one is configured.

    The two corpora fail independently. A vault page that cannot be embedded
    must not stall raw ingest, which the compile pipeline reads, and a raw
    outage must not stop the vault reaching the index — that second direction
    is W-2, the loop the whole retrieval inversion rests on.

    Readiness is the conjunction: the service is ready only when every
    configured watcher has completed a cycle, so a half-working sync reports
    unhealthy rather than ready.
    """
    marker = readiness_path or Path(
        os.environ.get("SYNC_READY_FILE", "/tmp/snp-sync-job/ready")
    )
    kwargs: dict[str, object] = {
        "base_delay": base_delay,
        "max_delay": max_delay,
        "sleep": sleep,
    }
    markers = [marker.with_suffix(".raw")]
    supervisors = [
        _supervise(indexer, raw_dir, markers[0], **kwargs)  # type: ignore[arg-type]
    ]
    if wiki_dir is not None:
        markers.append(marker.with_suffix(".wiki"))
        supervisors.append(
            _supervise(
                wiki_indexer or WikiIndexer(wiki_dir=wiki_dir),
                wiki_dir,
                markers[1],
                **kwargs,  # type: ignore[arg-type]
            )
        )
    supervisors.append(_aggregate_readiness(marker, markers, sleep=sleep))
    await asyncio.gather(*supervisors)
```

`_supervise` never returns in a live run — `watch()` runs until the process is
stopped — so the aggregate marker cannot be written after `gather`. It is
maintained by a third coroutine instead:

```python
async def _aggregate_readiness(
    marker: Path,
    children: Sequence[Path],
    *,
    interval: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Hold the service-level marker at the conjunction of its watchers.

    The health check reads one path. A half-working sync — vault indexing,
    raw stalled — must report unhealthy, so the marker is set only while
    *every* configured watcher is ready, and cleared the moment one is not.
    """
    while True:
        _set_readiness(marker, all(child.exists() for child in children))
        await sleep(interval)
```

Add `Sequence` to the `collections.abc` import in `scout/sync_job.py`.

Then in `main()` (`:419`), after `raw_dir` is resolved, add:

```python
    configured_wiki = os.environ.get("WIKI_DIR", "").strip()
    wiki_dir = Path(configured_wiki) if configured_wiki else None
```

and change the final call to:

```python
    if wiki_dir is not None:
        print(f"[sync-job] also watching {wiki_dir} -> PostgreSQL pgvector (vault)")
    asyncio.run(_async_main(indexer, raw_dir, wiki_dir=wiki_dir))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_sync_job.py --disable-socket -q
```

Expected: all pass, including the pre-existing `_async_main` tests. If any pre-existing test asserts the single-marker path, update it to the `.raw` suffix — **do not** delete it.

- [ ] **Step 5: Full offline suite + static**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest -m 'not integration' --disable-socket -q && \
uv run ruff check . && uv run ruff format --check . && uv run mypy scout scripts
```

Expected: 1387+ passed, static clean.

- [ ] **Step 6: Commit**

```bash
git add scout/sync_job.py tests/test_sync_job.py
git commit -m "feat(sync): watch the vault beside raw, with failure isolation

W-2 was open: push reached the replica and nothing indexed it. The stack ran
scout.serve and host-sync and no indexer at all.

Both watchers now run in one process with independent backoff. Readiness is
their conjunction, so a half-working sync reports unhealthy rather than ready.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Task 3: `snpmemory ingest-wiki` — the shipped operator entry point

Closes the rest of **E1**: an operator must be able to force a reindex through a shipped surface, not a script the maintainer runs by hand.

**Files:**
- Modify: `scout/cli/commands/wiki.py`, `scout/cli/declarations.py`
- Test: `tests/test_cli_wiki.py`

**Interfaces:**
- Consumes: `ingest_wiki` from `wiki_ingest.py:401`; `CommandResult` and the `Injected` config pattern used by `scout/cli/commands/ingest.py:53`.
- Produces: CLI command `ingest-wiki` with `--dir`, `--dry-run`, `-o json`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_wiki.py`:

```python
def test_ingest_wiki_reports_per_page_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command must report what it indexed, not merely exit zero."""
    from scout.cli.commands.wiki import ingest_wiki_command

    async def fake_ingest_wiki(
        wiki_dir: Path, **kwargs: object
    ) -> list[dict[str, object]]:
        return [
            {
                "source_uri": "a.md",
                "title": "A",
                "chunks_count": 3,
                "status": "indexed",
            },
            {
                "source_uri": "b.md",
                "title": "B",
                "chunks_count": 0,
                "status": "skipped_no_body",
            },
        ]

    monkeypatch.setattr("scout.wiki_ingest.ingest_wiki", fake_ingest_wiki)
    result = ingest_wiki_command(dir="wiki", dry_run=True)

    assert result.data["indexed"] == 1
    assert result.data["skipped"] == 1
    assert result.data["pages"] == 2


def test_ingest_wiki_is_declared_in_the_cli_manifest() -> None:
    """A command that is not in the manifest is not a shipped surface."""
    from scout.cli.declarations import COMMANDS

    names = {c.name for c in COMMANDS}
    assert "ingest-wiki" in names
```

Adjust `COMMANDS` to whatever the manifest collection in `scout/cli/declarations.py` is actually named — read it before writing this test; the assertion is on the shipped manifest, not on an import path.

- [ ] **Step 2: Run test to verify it fails**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_cli_wiki.py -k ingest_wiki --disable-socket -q
```

Expected: FAIL — `cannot import name 'ingest_wiki_command'`.

- [ ] **Step 3: Implement the command**

In `scout/cli/commands/wiki.py`:

```python
def ingest_wiki_command(
    *,
    dir: str = "wiki",  # noqa: A002 - matches the `ingest` command's flag name
    dry_run: bool = False,
    config: Injected = None,
) -> CommandResult:
    """Index every vault page into pgvector under the wiki corpus tier."""
    import asyncio
    from pathlib import Path

    from scout.wiki_ingest import ingest_wiki

    results = asyncio.run(ingest_wiki(Path(dir), dry_run=dry_run))
    indexed = sum(1 for r in results if r.get("status") == "indexed")
    skipped = len(results) - indexed
    return CommandResult(
        summary=f"{indexed} pages indexed, {skipped} skipped from {dir}",
        data={
            "pages": len(results),
            "indexed": indexed,
            "skipped": skipped,
            "results": results,
        },
    )
```

Match `CommandResult`'s real constructor signature — read `scout/cli/commands/ingest.py` and mirror it exactly rather than assuming these field names.

In `scout/cli/declarations.py`, beside the other `command(...)` registrations:

```python
command(
    "ingest-wiki",
    "Index every vault page into pgvector under the wiki corpus tier.",
    "scout.cli.commands.wiki:ingest_wiki_command",
    outcomes=_SEMANTIC,
    errors=(ErrorKind.INPUT_VALIDATION, ErrorKind.INFRASTRUCTURE),
    example=("--dir", "wiki", "-o", "json"),
    output_fields=(
        FieldSpec("pages", "integer"),
        FieldSpec("indexed", "integer"),
        FieldSpec("skipped", "integer"),
    ),
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_cli_wiki.py tests/test_cli_schema_conformance.py --disable-socket -q
```

Expected: pass. `test_cli_schema_conformance.py` validates `snpmemory schema` against `docs/CLI_SPEC.md` — if it fails, add the command to `docs/CLI_SPEC.md`; do not weaken the test.

- [ ] **Step 5: Verify the real surface**

```bash
.venv/bin/snpmemory ingest-wiki --dir wiki --dry-run -o json | head -20
```

Expected: JSON with `pages`, `indexed`, `skipped`. A dry run writes nothing.

- [ ] **Step 6: Commit**

```bash
git add scout/cli/commands/wiki.py scout/cli/declarations.py tests/test_cli_wiki.py docs/CLI_SPEC.md
git commit -m "feat(cli): ship ingest-wiki as an operator surface

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Task 4: Deploy `sync-job`; remove the orphan

Closes **E2** (declared, no container) and **E7** (orphan `basic-memory` still running).

**Files:**
- Modify: `docker-compose.yml` — the `sync-job` service at `:207`

- [ ] **Step 1: Record the starting state as evidence**

```bash
docker ps -a --filter name=snp-memory-sync-job --format '{{.Names}}' | wc -l   # expect 0
docker inspect snp-memory-it-basic-memory-1 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'
grep -c basic-memory docker-compose.integration.yml   # expect 0 — it is an orphan
```

- [ ] **Step 2: Give `sync-job` the vault**

In `docker-compose.yml`, in the `sync-job` service, add to `environment:`

```yaml
      WIKI_DIR: /vault-replica/current/wiki
```

and add a volumes block mounting the replica read-only, mirroring how `scout` receives it:

```yaml
    volumes:
      - vault-replica:/vault-replica:ro
```

Copy the exact volume name and syntax from the `scout` service rather than assuming; `scout` already mounts it.

Then check the service's `healthcheck`. It reads a single readiness path — the
one `SYNC_READY_FILE` names, default `/tmp/snp-sync-job/ready`. Task 2 keeps
that path as the aggregate marker maintained by `_aggregate_readiness`, so the
health check needs **no change**. Confirm that by reading the healthcheck line;
if it points at anything else, fix the path rather than the code.

- [ ] **Step 3: Bring it up and remove the orphan**

```bash
docker compose up -d sync-job
docker rm -f snp-memory-it-basic-memory-1
```

- [ ] **Step 4: Verify both, from inside the container**

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep sync-job
docker exec snp-memory-sync-job-1 sh -c \
  'for p in /proc/[0-9]*; do [ -r $p/cmdline ] && tr "\0" " " < $p/cmdline && echo; done' | grep sync_job
docker ps -a --format '{{.Names}}' | grep -c basic-memory   # expect 0
docker compose logs sync-job --tail 20 | grep "also watching"
```

Expected: a healthy `snp-memory-sync-job-1` running `python -m scout.sync_job`, a log line naming the vault directory, and no `basic-memory` container anywhere.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "fix(compose): actually run sync-job, and give it the vault

sync-job carried restart: unless-stopped and had never had a container. The
running stack was scout.serve, host-sync, gitea, litellm and postgres --
nothing indexed anything.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Task 5: Universal model stamp and a startup guard

Closes **E4**. 127 of 2303 chunks carry `metadata->>'model' = NULL`. The stamp exists to prevent two vector spaces in one index — F-2, the failure that justified deleting basic-memory — and at 94% coverage it does not guard.

**Files:**
- Modify: `scout/serve.py`
- Test: `tests/test_serve.py`

**Interfaces:**
- Produces: `assert_single_embedding_model(conn, *, expected_model: str) -> None`, raising `RuntimeError` on a mixed or unstamped index. Called from `serve.main()` before the server binds.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_startup_guard_rejects_a_mixed_index() -> None:
    """Two vector spaces in one index is F-2; it must be fatal, not silent."""
    from scout.serve import assert_single_embedding_model

    class Conn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            return [
                {"model": "gemini/gemini-embedding-001", "n": 2176},
                {"model": None, "n": 127},
            ]

    with pytest.raises(RuntimeError, match="unstamped"):
        await assert_single_embedding_model(
            Conn(), expected_model="gemini/gemini-embedding-001"
        )


@pytest.mark.asyncio
async def test_startup_guard_rejects_a_model_the_process_cannot_query_with() -> None:
    """An index built by a model the server does not use returns near-random order."""
    from scout.serve import assert_single_embedding_model

    class Conn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            return [{"model": "some/other-model", "n": 2303}]

    with pytest.raises(RuntimeError, match="some/other-model"):
        await assert_single_embedding_model(
            Conn(), expected_model="gemini/gemini-embedding-001"
        )


@pytest.mark.asyncio
async def test_startup_guard_accepts_a_single_matching_model() -> None:
    from scout.serve import assert_single_embedding_model

    class Conn:
        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            return [{"model": "gemini/gemini-embedding-001", "n": 2303}]

    await assert_single_embedding_model(
        Conn(), expected_model="gemini/gemini-embedding-001"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_serve.py -k startup_guard --disable-socket -q
```

Expected: FAIL — `cannot import name 'assert_single_embedding_model'`.

- [ ] **Step 3: Implement the guard**

In `scout/serve.py`:

```python
_MODEL_CENSUS = """
SELECT c.metadata->>'model' AS model, count(*) AS n
FROM rag_chunks c
GROUP BY 1
ORDER BY 2 DESC;
"""


async def assert_single_embedding_model(conn: Any, *, expected_model: str) -> None:
    """Refuse to serve an index built by more than one embedding model.

    Cross-space retrieval is the F-2 failure: a query embedded at 1024
    dimensions scored against vectors from another model returns near-random
    ordering **with no error**. It is indistinguishable from working, which is
    why it must stop the process rather than be logged.
    """
    rows = await conn.fetch(_MODEL_CENSUS)
    census = {row["model"]: int(row["n"]) for row in rows}
    unstamped = census.pop(None, 0)
    if unstamped:
        raise RuntimeError(
            f"refusing to serve: {unstamped} unstamped chunks in the index; "
            "re-ingest them so the embedding model is recorded"
        )
    if set(census) != {expected_model}:
        raise RuntimeError(
            f"refusing to serve: index holds {sorted(census)} but this process "
            f"embeds queries with {expected_model!r}"
        )
```

Call it from `main()` before the server binds, using `os.environ["LITELLM_EMBED_MODEL"]` as `expected_model`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest tests/test_serve.py --disable-socket -q
```

Expected: pass.

- [ ] **Step 5: Make the live index satisfy the guard**

The 127 unstamped chunks are `raw/` documents ingested before the stamp existed. Re-ingest them through the current path, which stamps. Record the counts before and after; a drop is a regression, not a success.

```bash
docker exec snp-memory-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT coalesce(metadata->>'"'"'model'"'"','"'"'<NULL>'"'"'), count(*) FROM rag_chunks GROUP BY 1;"'
.venv/bin/snpmemory ingest --dir raw --confirm
docker exec snp-memory-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT coalesce(metadata->>'"'"'model'"'"','"'"'<NULL>'"'"'), count(*) FROM rag_chunks GROUP BY 1;"'
```

Expected after: one row, `gemini/gemini-embedding-001`, total ≥ 2303.

- [ ] **Step 6: Verify the guard passes against the real stack**

```bash
docker compose up -d --force-recreate scout && sleep 15 && \
docker compose logs scout --tail 30 && \
docker ps --format '{{.Names}}\t{{.Status}}' | grep scout
```

Expected: healthy, no `refusing to serve`.

- [ ] **Step 7: Commit**

```bash
git add scout/serve.py tests/test_serve.py
git commit -m "feat(serve): refuse to serve an index built by two embedding models

The model stamp covered 2176 of 2303 chunks, so the guard against F-2 -- the
cross-space failure that justified removing basic-memory -- did not guard.
Cross-space retrieval returns near-random ordering with no error, so this is
fatal at startup rather than logged.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Task 6: W-1 acceptance oracle — find, read, cite

Closes half of **E5**. Today 2 of 123 checks measure engine behavior.

**Files:**
- Create: `artifacts/v3/checks/engine_acceptance.py`
- Modify: `artifacts/v3/retrieval_questions.json`
- Create: `.unlazy/v3-retrieval/gates/engine-2026-09-04.md`

**Interfaces:**
- Consumes: `ScoutDiyEngine`, `LiteLLMBatchEmbedder`, `Scope`, and the existing question schema `{"lang", "query", "expect"}`.
- Produces: `engine_acceptance.py --group find-read-cite` printing `FIND READ CITE VERIFIED`. Tasks 7 and 8 add groups to the same module.

- [ ] **Step 1: Extend the question set and add the absent-page control**

Grow `artifacts/v3/retrieval_questions.json` from 20 to at least 40 entries, keeping the English/Vietnamese balance — Vietnamese is load-bearing, because a 250 ms dense timeout once made every Vietnamese query return nothing while every mechanical gate stayed green. Add one entry with a deliberately absent page:

```json
  {"lang":"en","query":"quantum error correction surface codes","expect":"__ABSENT__/no-such-page.md","control":"absent"}
```

- [ ] **Step 2: Write the oracle with its control**

Create `artifacts/v3/checks/engine_acceptance.py`. Structure it on the existing `artifacts/v3/checks/*.py` pattern: `GateFailure`, `require(condition, message)`, a `GROUPS` dict, `--group` argparse, success-only tokens printed after every assertion.

`group_find_read_cite()` must:

1. Build the **shipped** engine — `ScoutDiyEngine.from_vault` with the production `PgVectorRlsBackend(corpus="wiki")`, not a test double.
2. For every non-control question: run `wiki_search`, require the expected page within the top 5, and record its rank.
3. For **every page any search returned**: call `wiki_read` and require a non-empty body. This is the check that would have caught the 431-vs-7 vault fork, where search returned five correct pages and read raised `KeyError` on all five.
4. For every read page: require the heading the answer would cite to exist in that page's own outline.
5. Require `recall@1 ≥ 0.60` and `recall@5 ≥ 0.85`. Read the floors from constants; never from the measured result.
6. **Control:** require the `"control":"absent"` question to be *missed*. If the oracle scores it as found, fail with `"the absent-page control was scored as found; this gate cannot tell present from absent"`.
7. Print `FIND READ CITE VERIFIED` only after all of the above.

- [ ] **Step 3: Run it and confirm it can fail**

```bash
set -a && . ./.env && set +a
.venv/bin/python artifacts/v3/checks/engine_acceptance.py --group find-read-cite
```

Expected: `FIND READ CITE VERIFIED`.

Then prove the control works by temporarily raising `RECALL_AT_1_FLOOR` to `0.99` and re-running. Expected: non-zero exit and no token. Restore the floor. **Record both outputs in the ledger evidence** — a gate never shown to fail is not evidence.

- [ ] **Step 4: Write the ledger entry**

Create `.unlazy/v3-retrieval/gates/engine-2026-09-04.md`:

```markdown
# Gates: engine completion

OWNS: .unlazy/v3-retrieval/gates/engine-2026-09-04.md, artifacts/v3/checks/engine_acceptance.py, artifacts/v3/retrieval_questions.json

Scope: The agent queries the index and gets back an address and a file, and a change in the vault reaches the index — both measured against the live corpus through shipped surfaces, both shown to fail against a control.

- [ ] E1: search finds the page, read returns its body, and the cited heading exists
  CHECK: set -a && . ./.env && set +a && .venv/bin/python artifacts/v3/checks/engine_acceptance.py --group find-read-cite
  EXPECT: FIND READ CITE VERIFIED
  EVIDENCE: pending
```

- [ ] **Step 5: Commit**

```bash
git add artifacts/v3/checks/engine_acceptance.py artifacts/v3/retrieval_questions.json
git commit -m "test(v3): measure whether the engine finds, reads and cites

Two of 123 checks in this scope measured engine behavior; the rest measured
source shape, contract shape or unit behavior against fakes. This one asks the
question the owner asks -- did it find my article, and could it read it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Task 7: W-2 acceptance oracle — a vault change reaches the index

Closes the other half of **E5**, and is the only proof that Tasks 1–4 actually closed the loop.

**Files:**
- Modify: `artifacts/v3/checks/engine_acceptance.py`, `.unlazy/v3-retrieval/gates/engine-2026-09-04.md`

**Interfaces:**
- Produces: `--group vault-change-propagates` printing `VAULT CHANGE PROPAGATES`.

- [ ] **Step 1: Write the oracle with both controls**

`group_vault_change_propagates()` must:

1. Pick a probe page and generate a unique sentinel, e.g. `V3-PROP-<uuid4 hex>`.
2. **Positive control first:** query for the sentinel through `wiki_search` and require **zero** hits. If the sentinel is already findable the test proves nothing.
3. Clone the vault repo to a temporary directory, write the sentinel into the probe page, commit and push to Gitea `main`. This is the owner-authorised vault-repo push; it must never touch the source repository.
4. Poll `wiki_search(sentinel)` with a bounded timeout — 180 s, 5 s interval — running **no other command**. Any manual `ingest` here would invalidate the entire gate.
5. Require the sentinel to become findable, and `wiki_read` on the returned page to contain it.
6. Restore the page and push the restoration.
7. **Negative control:** stop the vault watcher (`docker compose stop sync-job`), repeat steps 1–4 with a fresh sentinel, and require the poll to **time out**. Restart it. Without this the gate cannot distinguish "the loop works" from "the loop happened to be in the right state."
8. Print `VAULT CHANGE PROPAGATES`.

Restoration must run in a `try/finally` so a failure mid-gate does not leave a sentinel committed in the lead's vault.

- [ ] **Step 2: Run it**

```bash
set -a && . ./.env && set +a
.venv/bin/python artifacts/v3/checks/engine_acceptance.py --group vault-change-propagates
```

Expected: `VAULT CHANGE PROPAGATES`. Expect it to take several minutes: it performs two pushes and one deliberate timeout.

- [ ] **Step 3: Add the ledger entry**

```markdown
- [ ] E2: an edit pushed to Gitea changes the next answer, with no command run, and does not when the watcher is stopped
  CHECK: set -a && . ./.env && set +a && .venv/bin/python artifacts/v3/checks/engine_acceptance.py --group vault-change-propagates
  EXPECT: VAULT CHANGE PROPAGATES
  EVIDENCE: pending
```

- [ ] **Step 4: Commit**

```bash
git add artifacts/v3/checks/engine_acceptance.py
git commit -m "test(v3): prove a vault edit reaches the index, and does not without the watcher

Root G12 and node-1.3:N4 were both manual and both unrun, which is why nobody
noticed that the replica updated and the index never did.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

### Task 8: Replace the asyncpg double with a live-PostgreSQL gate

Closes **E6**. `e2e_retrieval.py` asserts SQL *text* and computes results in Python: substring match instead of hybrid ranking, `setdefault` instead of the `page_best` CTE, constant `rrf_score`, RLS as a set intersection.

**Files:**
- Modify: `artifacts/v3/checks/engine_acceptance.py`, `.unlazy/v3-retrieval/gates/engine-2026-09-04.md`

**Interfaces:**
- Produces: `--group live-sql` printing `LIVE SQL VERIFIED`.

- [ ] **Step 1: Write the gate with the fail-closed control**

`group_live_sql()` must run the real `PgVectorRlsBackend` against the real PostgreSQL and require:

1. A scoped query with all four canonical departments returns rows, and every row's `source_uri` is distinct — the `page_best` CTE doing real page-level dedup.
2. `corpus="wiki"` cannot reach a `raw/` document, while an unfiltered backend built in the same process can. Both halves run through real SQL.
3. `rrf_score` values are **not** all equal — proving real fusion rather than the double's constant.
4. **Fail-closed control:** a connection whose `scout.current_depts` is never set returns zero rows from `rag_chunks`, **and the gate fails if it cannot observe that**. This exact blindness produced two false "database empty" diagnoses against an index holding 2303 chunks; the gate must be able to see the condition, not be blinded by it.
5. Print `LIVE SQL VERIFIED`.

Leave the hermetic `e2e_retrieval.py` in place as a fast offline contract check, but update `node-1.2.md`'s N2 to cite this gate as the evidence for "crosses all four layers." The hermetic oracle stops being that evidence.

- [ ] **Step 2: Run it, and prove it fails when it should**

```bash
set -a && . ./.env && set +a
.venv/bin/python artifacts/v3/checks/engine_acceptance.py --group live-sql
```

Expected: `LIVE SQL VERIFIED`.

Then confirm the control: temporarily point the backend at `corpus="nonexistent"` and re-run. Expected: failure, not a pass on zero rows. Restore.

- [ ] **Step 3: Add the ledger entry and record all three gates**

```markdown
- [ ] E3: the shipped retrieval SQL runs against real PostgreSQL, with real RLS, and the gate can see fail-closed
  CHECK: set -a && . ./.env && set +a && .venv/bin/python artifacts/v3/checks/engine_acceptance.py --group live-sql
  EXPECT: LIVE SQL VERIFIED
  EVIDENCE: pending
```

Then record real evidence for all three through the runner:

```bash
node /home/ple/.claude/skills/unlazy/scripts/gate-check.mjs --reverify --approve --timeout 1800 \
  --cwd "$PWD" .unlazy/v3-retrieval/gates/engine-2026-09-04.md
```

Expected: `ALL MET (3 met, ...)`.

- [ ] **Step 4: Final verification of the whole engine**

```bash
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest -m 'not integration' --disable-socket -q && \
uv run ruff check . && uv run ruff format --check . && uv run mypy scout scripts && \
set -a && . ./.env && set +a && \
.venv/bin/python artifacts/v3/retrieval_quality.py
```

Expected: suite green, static clean, recall at or above the starting 0.80 / 1.00. **A recall drop is a regression introduced by this work** — investigate before reporting done.

- [ ] **Step 5: Commit**

```bash
git add artifacts/v3/checks/engine_acceptance.py .unlazy/v3-retrieval/gates/engine-2026-09-04.md
git commit -m "test(v3): run the shipped retrieval SQL against real PostgreSQL

The only integration gate modelled Postgres in Python -- substring match for
hybrid ranking, setdefault for the page_best CTE, a constant rrf_score, RLS as
a set intersection. It could not have caught a defect in any of them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MeJqAkos2obJpFN6kZj543"
```

---

## Completion criteria

The engine is done when **all** of these hold, measured immediately before reporting:

1. `FIND READ CITE VERIFIED`, `VAULT CHANGE PROPAGATES`, `LIVE SQL VERIFIED` — all three, through the gate runner, with real `EVIDENCE:` lines.
2. Each of the three has been **observed failing** against its control, and both outputs are recorded.
3. Offline suite green (≥ 1387 passed), `ruff check`, `ruff format --check`, `mypy scout scripts` all clean.
4. `docker ps` shows a healthy `snp-memory-sync-job-1` and **no** `basic-memory` container.
5. `retrieval_quality.py` reports recall at or above 0.80 / 1.00.
6. Nothing added by this plan is declared and unwired.

Anything unmet is reported as unmet, with its number. A gate that cannot be met is marked `ABANDON: <id> <reason>` and surfaced as a handoff — abandonment ends execution honestly, but it is never success.
