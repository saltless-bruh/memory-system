# node-3 close-out — ingest integrity

**Verdict: closed.** Every gate met, nothing abandoned, nothing deferred.
All figures below were re-measured on 2026-09-21 immediately before writing,
not copied from an earlier report.

Branch subject: *a transient gateway failure during ingest is retried, visible,
and cannot be silently lost.*

```text
  ┌── what a PDF ingest does now ────────────────────────────────────────┐
  │                                                                      │
  │  cold start ─▶ probe_gateway(snp-embed, snp-vlm)        ◀── leaf-3.3 │
  │                   │  503 / refused → wait, readiness cleared         │
  │                   ▼  answers                                         │
  │              parse_pdf ─▶ figure ─▶ urlopen_with_retry  ◀── leaf-3.1 │
  │                              │        5 attempts, bounded            │
  │                              ▼                                       │
  │              described == 0 → "failed", not "partial"   ◀── leaf-3.2 │
  │                              │                                       │
  │                              ▼                                       │
  │              rag_documents.extraction_status            ◀── 008      │
  │                   │                    │                             │
  │                   ▼                    ▼                             │
  │           verify-extraction     ingest-integrity                     │
  │           (names documents)     (names them too)                     │
  └──────────────────────────────────────────────────────────────────────┘

  The vault tier waits only for snp-embed: it is Markdown and never calls the
  vision route, so a dead describer cannot stall the corpus that never used it.
```

## Gate ledger, as measured

| Ledger | Gates | Met | Unmet | Abandoned |
|---|---|---|---|---|
| `leaf-3.1` vision retry | 3 | 3 | 0 | 0 |
| `leaf-3.2` extraction state + visibility | 5 | 5 | 0 | 0 |
| `leaf-3.3` cold-start gateway readiness | 3 | 3 | 0 | 0 |
| `leaf-3.4` corpus repair | 3 | 3 | 0 | 0 |
| `leaf-3.5` sync-job liveness | 3 | 3 | 0 | 0 |
| `node-3` branch | 3 | 3 | 0 | 0 |
| **total** | **20** | **20** | **0** | **0** |

`node-3:N1` re-verified all 17 leaf gates from scratch, by execution, not from
stored evidence. `node-3:N2` measured the offline suite: **1781 passed,
0 failed** against a recorded baseline of 3 failed / 1754 passed.

## Contract rows

| Row | State |
|---|---|
| C-3.1 transient gateway failure is retried | DONE |
| C-3.2 `described == 0` is distinct from a partial | DONE |
| C-3.3 cold start waits for the gateway, not `depends_on` | DONE |
| C-3.4 a failed extraction is visible and nameable | RE-SCOPED by owner, then DONE |
| C-3.5 the 7 figure descriptions are in the index | ALREADY TRUE, re-verified |
| C-3.6 `sync-job` is running and propagating | DONE |
| C-3.7 `ingest-integrity` passes live | DONE |
| C-3.8 a publish race must not stop the vault watcher | DONE |
| C-3.9 a verification surface must not pass over an index it cannot read | DONE |

## What was refuted, and what that cost

Five gate refutations were performed: each fix was watched failing before it was
watched passing. They are recorded in full under `node-3:N3`. Beyond those, four
things in this branch were found to be **wrong rather than missing**, and each
correction is in `docs/AUDIT_2026-09-15.md`:

1. **C-3.4's mechanism was false.** The staleness short-circuit is wiki-only and
   that tier has no failable extraction (0 wiki chunks carry `figures_status`,
   0 raw chunks carry `content_hash`). Building the refusal would have been an
   unreachable branch. The owner re-scoped it to visibility, which shipped.
2. **The 7 figures were already restored** before this session, by a re-ingest
   hours after the audit measured them missing. The `PARSER_REVISION` bump is
   owed for honesty, not repair.
3. **`extraction_visible.py` broke the system it measured.** Roles are
   cluster-wide and migrations 002/003 issue an unconditional
   `ALTER ROLE ... NOLOGIN`, so applying the ladder to a scratch *database*
   revoked login for the live roles on every run. It now starts its own
   throwaway PostgreSQL container. The audit's earlier claim that this was "not
   caused by any change in this session" was wrong and has been corrected.
4. **`verify-extraction` passed over an index it never read.** It read
   RLS-filtered `rag_documents` without setting `scout.current_depts`, saw an
   empty table, and reported `status: pass, checked: 0` over a 439-document
   index — the exact failure shape it exists to catch. Now scoped, and zero rows
   is no longer a pass. The same defect was found and fixed in
   `corpus_repair.py`.

One test was found to be asserting nothing:
`test_wiki_indexer_reports_a_config_fault_as_permanent` passed `tmp_path`, which
exists, so it demanded "permanent" for a vault that was present.

## Live state after the rebuild

| Check | Result |
|---|---|
| `snpmemory status` | `ok`, nothing unhealthy, nothing stopped |
| `snpmemory verify-extraction` | 441 checked, 441 complete, 0 incomplete, 0 unknown |
| `engine_acceptance --group ingest-integrity` | VERIFIED — `figures_status {ok: 135}`, coverage 7/7, 0 unrecorded |
| `corpus_repair.py --verify` | FIGURES RESTORED — 25 figure chunks, 7/7 described |
| RAG roles | `rag_app_role` and `rag_ingest_role` both `rolcanlogin = t` |
| Vault watcher | survived a probe page being indexed and deleted (`deleted_count: 1`) |

Cost paid: one full re-embed of 436 documents for the `PARSER_REVISION 2 → 3`
bump, plus a re-describe of the PDF's 7 figures through `snp-vlm`. The second
rebuild re-embedded nothing (`indexed_count: 0, unchanged_count: 439`).

## Carried forward — not part of this branch

- **Nothing is committed.** `HEAD` is still `6f143d7` with 112 dirty paths. The
  images were built from the working tree but stamped with `HEAD`, whose tree is
  not what they contain. A rebuild after the eventual commit makes the stamp
  true.
- **Docker's embedded DNS fails intermittently.** `127.0.0.11` returns 0 answers
  while the same container resolves directly against the router, 8.8.8.8 and
  1.1.1.1. It took the gateway down twice; restarting the container clears it.
  An environment fault, not a repository one — no code change was made for it.
- **The migration sharp edge is left as found.** Re-running the ladder anywhere
  in a cluster silently disables both roles until `provision_postgres_roles.py`
  runs again. Fail-closed by design, but nothing warns that a scratch database
  is not a scratch cluster.
- `docs/AUDIT_2026-09-15.md` disposition for the public `origin` is still an
  open owner decision.
