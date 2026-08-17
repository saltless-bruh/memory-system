# Phase 0 — Gate Results Ledger

> **Exit condition for Phase 0 (tasks.md):** all 4 gates have a recorded
> conclusion. If ≥1 gate forces a branch, update `design.md` *before*
> starting Phase 1.

| Gate | Task | Question | Result | Evidence | Decided |
|---|---|---|---|---|---|
| 1 — Git↔index sync | T-0.5 | Does basic-memory's index stay consistent under Git commits + concurrent writers; does `doctor` catch drift? | ✅ **PASS — SQLite adequate for V1** | live 2026-07-21 | 2026-07-21 |
| 2 — sources[] passthrough | T-0.6 | Does basic-memory preserve `sources[]` byte-for-byte across index + write_note? | ✅ **PASS via config** — basic-memory 0.22 rewrites frontmatter by default (injects `permalink`); with `disable_permalinks:true` + `ensure_frontmatter_on_sync:false` it makes **0 file writes**, vault pristine | live 2026-07-21; `docs/basic-memory-setup.md` | 2026-07-21 |
| 3 — AGPL policy | T-0.3 | Does company policy allow an AGPL-3.0 dependency for internal self-host? | ✅ **ALLOWED** (provisional — build-to-evaluate, per lead) | `gate3_agpl_license/DECISION_MEMO.md` | 2026-07-21 |
| 4 — Vietnamese recall | T-0.7 | Which embedding model gives adequate VN paraphrase recall for wiki-search? | ✅ **SWITCH TO bge-m3** (FastEmbed default inadequate for VN) | stdout of `run_gate4.py` (transcript write sandboxed in authoring env) | 2026-07-20 |

## Gate 4 — full result (concluded)

Ran `python spikes/gate4_vietnamese_recall/run_gate4.py` against the 7-page
sample vault, 16 Vietnamese questions (8 keyword-free paraphrases).

| Backend | Model | recall@1 | recall@3 | recall@5 | paraphrase-only @3 |
|---|---|---|---|---|---|
| FastEmbed default | `BAAI/bge-small-en-v1.5` | 0.625 | 0.812 | 0.875 | 0.875 |
| **bge-m3 via LiteLLM** | `ollama/bge-m3` | **0.812** | **1.000** | **1.000** | **1.000** |

**Finding:** the English-only FastEmbed default misses hard on Vietnamese
paraphrases — the AD CS ESC8 paraphrases rank **7th**, with navigational
pages (`archive`, `log`) beating the real target. bge-m3 retrieves every
question's correct page within top-3, paraphrases included (R-2.3).

**Decision (R-2.6, R-8.4.4):** unify wiki-search on **bge-m3**, matching
the RAG embedder. Feeds T-1.4. No `design.md` change needed — this is the
design's stated preferred branch (design §2.2).

## Gate 1 — findings (concluded)

Run live against the running MCP server (watch service active) on the
7-page vault. basic-memory 0.22 has **no `bm sync`** command — the
filesystem watch inside the MCP server does sync; `bm doctor` verifies
consistency.

| Test | Result |
|---|---|
| External edit (append to `log.md`, not via basic-memory) | Watch indexed it within ~8s; `status: No changes` |
| Rapid successive edits (4× churn) | `status: No changes`; **`Doctor checks passed`** |
| Git-operation reconciliation (`git checkout -- wiki/log.md`) | Index reconciled to reverted content; `status: No changes`; 7 entities |

**Decision: SQLite is adequate for V1.** The watch + `doctor` keep the
file↔index consistent under external Git-driven changes and churn. Matches
design §7 / R-8.4.1: SQLite for V1, **Postgres deferred to V2** for heavy
multi-writer concurrency (not exercised here — V1 is read-heavy).

Operational notes (see `docs/basic-memory-setup.md`):
- The incremental scan is checksum-based; a polluted scan-state can make
  sync report "0 changes" with 0 entities — `bm reset` recovers.
- `watch_project_reload_interval` is 300s, but file-level watch is near
  real-time (edits synced in seconds).
- `bm doctor` round-trips write→sync→search→consistency and is the
  reconciliation tool of record.

## Branch decisions triggered

- Gate 1 → if FAIL after Postgres: Scout-DIY primary (T-2.4). _[not yet run]_
- Gate 2 → if FAIL: move address to body-block/sidecar, update design §3. _[not yet run]_
- Gate 3 → if DENIED: Scout-DIY primary, drop T-1.x. _[awaiting human decision]_
- Gate 4 → **FIRED**: FastEmbed default inadequate → wiki-search uses bge-m3. No design change (design already prefers this).

## Overall Phase 0 status

**2 of 4 gates resolved.** Gate 4 concluded (bge-m3). Gates 1 & 2 need
basic-memory, which is gated on Gate 3 (the AGPL human decision). Gate 3 is
the remaining blocker for the basic-memory branch.

Infra verified alongside the gates (2026-07-20, Docker + Ollama up):
- **T-0.1**: `git` (Gitea) + `litellm` containers up & healthy. Push/clone
  pending Gitea admin bootstrap (a human account step).
- **T-0.2**: `snp-embed`, `snp-llm`, `snp-vlm` all answer **through the
  LiteLLM chokepoint** on local Ollama. Physical no-egress cut is the
  operator's final confirmation (architecturally guaranteed: local
  `api_base`, no cloud fallback).
