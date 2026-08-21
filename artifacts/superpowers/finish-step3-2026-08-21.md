# Finish: Step 3 — Multi-Article Compilation

Branch: `fix/architecture-security-hardening` · Plan: `artifacts/superpowers/plan.md`
Completed: 2026-08-21

## Result

One source becomes N grounded, cross-linked pages, and a batch that fails part-way writes
nothing.

| Check | Result |
|---|---|
| `ruff check .` · `mypy scout scripts` | clean (53 source files) |
| `pytest -q` | **728 passed**, 21 pre-existing live-integration env errors |
| `verify_addresses.py` (live) | **5 PASS · 0 FAIL · 0 DRIFT** |
| `verify_groundedness.py` (live) | **exit 0 — 5 GROUNDED · 0 UNSUPPORTED · 2 UNSOURCED** |
| `gen_index.py --check` | clean |
| Cross-links | resolve in **both directions** across all 3 batch pages |

## The four problems, and what actually answers them

**P2 — decomposition.** The paper declares its own structure: 31 numbered headings.
`plan_articles.py` extracts them with **no model call**, dedups the repeats that ToCs and
running headers produce, and orders numerically (`2.2 < 2.10 < 10`). Output is
**byte-identical across runs** (sha256 equal ×3).

This is the answer to idempotency, and it is not the obvious one: byte-reproducible LLM
output is not achievable, so the skeleton is stable because it is **never regenerated** —
not because generation was made deterministic. A source with no numbered headings raises
and says it needs a hand-written plan rather than guessing.

**P3 — cross-references.** All slugs are collected before any body is generated, so article
1 may link to article 5. Verified live in both directions.

**P4 — batch minting.** Pre-flight mints every article before any generation. The code is
explicit that minting from the title alone is **sufficient but not necessary** — compilation
also tries a model-generated hint — so a failure reports UNCERTAIN and `--allow-uncertain`
proceeds. Overstating that check would have been the easy lie.

**P5 — atomicity.** `compile_note` split into `prepare_page` (writes nothing) and
`publish_page`. A batch prepares everything into staging, and only a fully prepared batch
publishes, compensating in reverse order. **This is a saga, not a transaction**, and the
module says so: a kill between two renames is still not atomic.

**P7 — cost.** Each staged page is checkpointed the moment it is prepared. Demonstrated
live: after a dry run, the real publish **resumed from staging and spent zero model calls**.

## The prerequisites that mattered more than expected

`JUDGE_CONCURRENCY = 3` was hardcoded and there was **no backoff anywhere in the codebase**.
Before this work, judging two pages at once returned exit 2 INFRASTRUCTURE. After the
concurrency ceiling (default 1) plus jittered backoff honouring `Retry-After`, the whole
vault judged in **one run with no 429**.

Generation also set no temperature at all while the judge already pinned it to 0.

## Review pass

**Blocker** — none.

**Major — step 10 pulls in the previously-uncommitted CLI foundation.** `plan-articles` and
`compile-plan` live in `scout/cli/commands/compile.py`, which requires `scout/cli/` — the
deliverable you had deliberately left uncommitted. Committing step 10 commits that
foundation too. It is on a feature branch and unpushed, so it is fully revertible, but it
is a scope expansion you did not explicitly approve.

**Minor — pre-flight mints twice.** Pre-flight mints from the title; `prepare_page` mints
again with the model hint. Retrieval only, no model cost, and the redundancy is what keeps
`prepare_page` self-contained.

**Minor — heading regex requires 4+ character titles.** A section literally titled "AI"
would be missed. Caught by a test using "Top"; noise filtering is worth the trade, and the
plan file is hand-editable.

**Minor — `--dry-run` leaves staging behind** (deliberately, so the real run can resume),
but nothing prunes a stale staging directory if a plan is later edited. A resumed run would
reuse pages generated from the *old* plan. Worth a plan-hash guard.

**Nit** — the free tier needs `LITELLM_TIMEOUT_SECONDS=120` and concurrency 1; on a paid
route raise `SNP_JUDGE_CONCURRENCY`.

## Corrections made during execution

- **A malformed `Retry-After` would have crashed the retry path it exists to protect.**
  `email.utils.parsedate_to_datetime` raises on Python 3.14 rather than returning `None`.
  Caught by its own test.
- **A missing pre-flight line** in the first backgrounded run was investigated rather than
  assumed: `run_preflight` returns 3/3, and it was stdout/stderr interleaving in the
  capture.
- **Two test-data errors of mine** (wrong `ParsedDocument` constructor, a 3-character
  heading below the regex floor) were fixed in the tests, not worked around in the code.

## Follow-ups

1. Decide whether to keep the CLI foundation committed (see Major above).
2. Guard staging against a plan edit (hash the plan into the staging directory).
3. The 15-article plan is committed but only 3 were compiled — the other 12 are available
   whenever you want a fuller vault.
4. Next: MCP server, generated from the same `registry.py` records rather than written
   twice.
