# Engine Completion — Design

**Date:** 2026-09-04
**Branch:** `feat/v3-retrieval-inversion`
**Status:** design, approved for planning

## 1. Scope

The **engine** is two claims and nothing else:

1. **W-1 — the agent queries the index and gets back an address and a file.**
   `wiki_search` returns page identities; `wiki_read` returns the page body;
   the answer cites the page and heading it used.
2. **W-2 — a change in the vault reaches the index.**
   An edit pushed to Gitea changes the next answer, with no operator command.

Everything else in the V3 blueprint is out of scope for this document:
inspector, similarity graph, source fetch/cache, blue-green embedding
migration, `references.py`, figure/table extraction, the `verify-groundedness`
rework, the address-minting removals, the 433-page body-contract migration, and
the ledger consolidation. They are real work; none of them is the engine. A car
with an incomplete engine does not run, whatever else is fitted to it.

The governing rule for everything in scope: **a component is either working
through its real production path, or it is deleted. There is no third state.**
No placeholders, no cosmetic surfaces, no declared-but-unwired functions, no
`TODO`.

## 2. Measured starting state

Every number below was measured on 2026-09-04 against the live stack and the
433-page corpus, not read from a report.

### 2.1 What already works

| Component | Evidence |
|---|---|
| Hybrid retrieval, RRF, page-level dedup, per-class fusion | `retrieval_quality.py`: recall@1 = 0.80, recall@3 = 1.00, recall@5 = 1.00 over 20 questions, 10 English + 10 Vietnamese |
| Body indexing (not `summary`) | 2176 wiki chunks carrying real body text |
| Corpus tier | wiki queries cannot reach `raw/`; unfiltered backend still can |
| Vietnamese sparse arm | migration 007, `tsv_simple`, OR-fused |
| `wiki_read` reads disk, not the index (INV-3) | disk-refresh control: index holds sentinel A, file rewritten to sentinel B, read returns B and never A |
| Read coverage | 431 of 431 documents the index can return are readable |
| Read envelope, four modes, 40-word snippet bound, `seen` dedup | `diy_engine.py:41,42,65,165` — all wired |
| RLS, fail-closed | `rag_app_role`, `rag_ingest_role`, 6 policies |
| Authenticated MCP surface | `wiki_search` + `wiki_read`, static and JWT branches |
| W-2 transport half | a real push advanced the replica with no manual trigger |

### 2.2 Engine defects

| ID | Defect | Evidence |
|---|---|---|
| **E1** | `ingest_wiki()` is complete and **nothing calls it** | `scout/wiki_ingest.py:401`; sole importer is `diy_engine.py:26`, for read-path helpers. The 431 indexed documents were placed by a human invoking it by hand. |
| **E2** | `sync-job` is declared with `restart: unless-stopped` and **has no container** | `docker-compose.yml:207`; `docker ps -a` shows none |
| **E3** | No vault watcher. `sync_job` watches `raw_dir` only | `sync_job.py:137,284,299`. Full stack process list: `scout.serve`, `uvicorn host_sync:app`, gitea, litellm, postgres. **Nothing indexes.** |
| **E4** | Model stamp covers 2176 of 2303 chunks; no startup guard | 127 chunks carry `metadata->>'model' = NULL`. The stamp exists to prevent two vector spaces in one index — F-2, the failure that justified deleting basic-memory. It does not currently guard. |
| **E5** | 2 of 123 checks measure engine behavior | census of every `CHECK:` line in the scope. 121 measure source shape, contract shape, static typing, or unit behavior against fakes. |
| **E6** | The only integration gate models PostgreSQL in Python | `e2e_retrieval.py` doubles the asyncpg boundary: substring match instead of hybrid ranking, `setdefault` instead of the `page_best` CTE, constant `rrf_score`, RLS as a Python set intersection. |
| **E7** | Orphan `basic-memory` container is running | created 2026-08-18, from a compose config that no longer declares it; still holds the FastEmbed@384 space |

**E3 is the defect that matters most.** The W-2 chain is:

```
push → Gitea → webhook → host-sync → replica files updated → ✗ nothing
```

The transport half is proven. There is no indexer at the far end. The index is
a frozen snapshot. If a page is edited during a demonstration, the file lands in
the replica and the answer does not change.

### 2.3 Explicitly verified as *not* engine defects

- **`section` read mode.** Reached by passing the `section` parameter, which
  derives the mode at `diy_engine.py:487`. Real.
- **`## Cross-References` at 0/433.** `Page.wikilinks` is extracted by
  `_WIKILINK_RE` from the page **body**, independent of heading. 369 of 433
  pages already carry `[[wikilinks]]`; 195 carry them under a differently-named
  heading. The engine already has the relationship graph. The missing heading
  is a lint-contract item, not an engine input.
- **Rename handling.** `reconcile_deletions` (`ingest.py:424`) treats a rename
  as delete-old plus insert-new. The index ends up correct: the page is
  findable at its new path. §6.2's `content_hash` match preserving `doc_id` is a
  refinement, not a correctness fix, and is out of scope.

## 3. Architecture

No new subsystem. Every piece the engine needs already exists; three of them
are not connected to anything.

```
                    ┌─────────────────────────────────────────┐
   Obsidian ──push──▶ Gitea ──webhook──▶ host-sync ──▶ replica │  PROVEN
                    └─────────────────────────────────────────┘
                                                          │
                                       ┌──────────────────▼──────────────────┐
                                       │  sync-job (NOT RUNNING — E2)        │
                                       │    watcher A: raw_dir   → existing  │
                                       │    watcher B: wiki_dir  → NEW (E3)  │
                                       │      └─ WikiIndexer ─┐              │
                                       └──────────────────────┼──────────────┘
                                                              │
                            ┌─────────────────────────────────▼─────────────┐
                            │ ingest_wiki()  ← EXISTS, UNCALLED (E1)        │
                            │ reconcile_deletions()  ← EXISTS               │
                            └─────────────────────────────────┬─────────────┘
                                                              ▼
                                                   PostgreSQL / pgvector
                                                              │
                    ┌─────────────────────────────────────────▼─────────────┐
                    │ wiki_search → wiki_read → cite   (PROVEN, recall@5=1) │
                    └───────────────────────────────────────────────────────┘
```

The work is wiring, deployment, one guard, and — separately — replacing gates
that measure shape with gates that measure behavior.

### 3.1 The `RagIndexer` seam

`scout/sync_job.py:70` already defines the seam:

```python
@runtime_checkable
class RagIndexer(Protocol):
    async def index(self) -> IndexOutcome: ...
```

`IndexOutcome` is `(ok: bool, status: str, retryable: bool = False)`.
`PgVectorDirectIndexer` implements it for `raw/`. A `WikiIndexer` implementing
the same protocol for the vault is the whole of the new indexing code, because
`sync_once`, `watch`, `_async_main` and the backoff discipline are already
written and already parameterised by indexer and directory.

### 3.2 Two watchers, one process

`_async_main` currently drives one indexer over one directory. The vault
watcher runs beside it in the same process, on its own directory, with its own
indexer and its own readiness marker. Both are supervised by the existing
cold-start backoff, which exists because an earlier version produced 238
container restarts in a day; that discipline is retained rather than
reimplemented.

Failure isolation is required: a vault-ingest failure must not stop the raw
watcher, and the reverse. Readiness is the conjunction — the service reports
ready only when both watchers have completed a cycle.

### 3.3 The model guard

The guard is the reason F-2 cannot recur. It asserts at startup that every
chunk reachable through the served corpus tier carries exactly one embedding
model, and that the model matches the one the running process is configured to
embed queries with. A mismatch is fatal: serving a mixed index returns
near-random ordering with no error, which is precisely the failure that is
indistinguishable from working.

The 127 unstamped chunks are `raw/` documents ingested before the stamp
existed. They are re-ingested through the current path so the stamp is
universal, rather than being back-filled with a guess about which model
produced them.

## 4. Acceptance

The engine is complete when both claims are re-runnable measurements, not
checkboxes.

### 4.1 W-1 — find, read, cite

An oracle that, against the live index and the live corpus:

- asks each question in the question set through the **shipped** engine;
- requires the expected page within the top *k*;
- requires `wiki_read` to return a body for **every** page returned by search;
- requires each returned page's cited heading to exist in the page it was
  read from;
- and passes only above the recall floors.

Current floors are `recall@1 ≥ 0.60` and `recall@5 ≥ 0.85`, against a measured
0.80 and 1.00. The question set grows from 20; the floors do not move to
accommodate a result.

**Control:** a question whose expected page is absent from the corpus must make
the oracle fail. An oracle that cannot distinguish present from absent is worth
nothing.

### 4.2 W-2 — a vault change reaches the index

An oracle that:

- records the current answer to a probe query;
- writes a distinctive sentinel into a page and pushes it to Gitea;
- waits, with a bounded timeout, for the answer to change **with no other
  command run**;
- requires the new answer to contain the sentinel;
- and restores the page.

**Controls, both required:**
- *Positive:* the sentinel must be absent from the answer before the push. A
  gate that passes because the sentinel was always there proves nothing.
- *Negative:* with the vault watcher stopped, the same sequence must **fail**.
  This is what distinguishes "the loop works" from "the loop happens to be in
  the right state."

### 4.3 Live SQL

The hermetic double is replaced by a gate that runs the real
`PgVectorRlsBackend` against the real PostgreSQL: real RRF, real `page_best`
CTE, real RLS policies, real corpus tier. The hermetic oracle may remain as a
fast offline contract check, but it stops being the evidence for
"one authenticated call chain crosses all four layers."

**Control:** a connection with no clearance must return zero rows *and the gate
must fail*, proving the gate can see the fail-closed condition rather than
being blinded by it. This exact blindness produced two false "database empty"
diagnoses against an index holding 2303 chunks.

## 5. Out of scope, recorded so it is not silently dropped

| Item | Status |
|---|---|
| Address-minting removals (`mint`, `heal`, `gate`, `verify-addresses`) and the verification-chain split | Not engine. ~0.5d. `check` will break unless the chain is split first (`verify.py:263`). |
| 433-page body contract (TL;DR 12/433, Provenance 91/433, Cross-References 0/433) | Not engine. Cross-References is generatable for 369 pages plus 195 heading renames. |
| Index inspector, similarity graph, source fetch + content-addressed cache, `read_source`, blue-green migration, `references.py`, figure/table extraction, `verify-groundedness` rework | Not engine. Each is either built properly later or deleted; none stays declared-and-hollow. |
| Ledger consolidation (28 → 4), 15 duplicated `OWNS:` paths | Process debt, not engine. |
| `gitea-runner` not running, so CI `checks`/`security` have never executed | Not engine. |
| Blueprint §8.5 lists `pgvector.py` and `ingest.py` as "retained without change"; this branch changed them by +212/−56 and +17. §8.7's "70% retained unchanged" is therefore wrong. | Document correction. |

## 6. Risks

| Risk | Mitigation |
|---|---|
| Two watchers in one process; a vault failure stalls the raw watcher or vice versa | Failure isolation is a tested requirement, not an assumption. Each watcher owns its readiness state; the service is ready only when both are. |
| `ingest_wiki` embeds through LiteLLM; a watcher on a busy vault could issue large paid batches on every save | The watcher debounces through the existing `watchfiles` adapter, and `ingest_document` is idempotent — unchanged pages re-upsert without re-embedding. Verified as part of the incremental-reindex test, not assumed. |
| Re-ingesting `raw/` to fix the 127 unstamped chunks changes the corpus the compile pipeline reads | `raw/` ingest is idempotent and content-hashed. The chunk count is asserted before and after. |
| The W-2 oracle pushes to the vault repository's `main` | Already covered by the owner's recorded exception for vault-repo pushes. It writes a sentinel and restores it; it never touches the source repository. |
| Estimates | Two earlier estimates in this effort were quoted without checking and were wrong by more than an order of magnitude. Every task in the plan is sized against code that has been read. |
