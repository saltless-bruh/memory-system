# Implementation Plan: Step 3 — Multi-Article Compilation

Branch: `fix/architecture-security-hardening` · Base: `715faf7`
Prepared: 2026-08-21

> The previous occupant (Grounded Page Bodies, complete — 688 tests, both live pages
> GROUNDED under an independent judge) was archived to
> `artifacts/superpowers/plan-grounded-bodies-2026-08-21.md`.

---

### Goal

Compile one large source into **N grounded, cross-linked wiki pages** in a single
reviewable operation, where every page passes the address gate and the groundedness gate,
and a failure part-way leaves the vault byte-identical to how it started.

Step 3 is the batch. The unit — one grounded page — already works and is not revisited.

**Definition of done:** `snpmemory compile-plan` turns the 26-page paper into ~8 pages that
`verify_addresses.py` and `verify_groundedness.py` both pass, with working `[[wikilinks]]`
between them, and a killed mid-run leaves `wiki/` unchanged.

---

### The finding that reshapes P2

**The document already declares its own structure.** Scanning the paper's text finds **31
numbered headings** — `1 Introduction`, `2.2.1 Supervised Learning`, `2.6.4 Model Training
and Execution Time`, `3.6 Clinical Imaging` …

So decomposition does not have to be invented by a model. It can be **extracted
deterministically** from the source, and the only judgement left is *which* sections
deserve a page and where to merge them — a small, reviewable decision rather than an
open-ended generative one.

This matters because of BP-1 below: LLM output is not byte-reproducible, so anything we
want to be stable must not be re-generated on each run. Deriving the split from the
document's own headings makes the skeleton stable **by construction**, and the approved
plan file makes the rest stable **by pinning**.

Caveat found while probing: headings repeat (`2.5` twice, `3` three times) because running
headers and the TOC re-emit them. Dedup by first occurrence, ordered by page.

---

### Assumptions

1. The unit is fixed. `compile_note` generates from retrieved passages and self-judges;
   Step 3 orchestrates it and does not change how one page is written.
2. `snp-judge` stays a different model family from `snp-llm` (self-preference bias).
3. The judge runs on a **free** OpenRouter tier: `JUDGE_CONCURRENCY = 3` already returned
   429, and `LITELLM_TIMEOUT_SECONDS=120` is required.
4. Departments come from `raw/.acl.yaml` (`papers/** → [ai_eng, blueteam]`), never invented
   per page.
5. PR-first (R-6.4/R-7.3); batch output is reviewed before merge like any page.

---

### Plan

**1. Archive the completed plan, land this one**
- Files: `artifacts/superpowers/plan.md`, `plan-grounded-bodies-2026-08-21.md`
- Verify: `ls artifacts/superpowers/`; `head -4 plan.md`.

**2. Bound generation variance (BP-1 prerequisite)**
- Files: `scripts/compile_note.py`
- Change: `_chat_completion` sends **`temperature: 0`**. The judge already does; generation
  does not, so today the one place variance actually matters is unpinned.
- Also record the resolved model name in the page's `last_compiled` neighbourhood or the
  batch manifest, so a page can be traced to what produced it (version pinning).
- Verify: unit test asserts `temperature == 0` in the request body; `pytest -q`.

**3. Rate-limit resilience (BP-3) — before N multiplies it**
- Files: `scripts/compile_note.py`, `scripts/verify_groundedness.py`
- Change:
  - retry on HTTP 429 and 5xx with **exponential backoff + jitter**, honouring
    `Retry-After` when present; bounded attempts, then fail;
  - `JUDGE_CONCURRENCY` becomes env-configurable (`SNP_JUDGE_CONCURRENCY`, default 1 for
    free tiers) instead of a hardcoded 3.
- Verify: unit tests with a fake `urlopen` raising 429 then succeeding — assert it retried,
  slept, and honoured `Retry-After`; assert a 429 storm still terminates.

**4. Deterministic decomposition — `plan_articles`**
- Files: `scripts/plan_articles.py` (new), `tests/test_plan_articles.py` (new)
- Change: extract numbered headings + their page ranges from a parsed source; dedup
  repeats by first occurrence; emit a **JSON plan**: `{title, section, loc, category,
  department, slug}` per proposed article.
- No model call. Pure function of the document.
- Verify: run on the paper → a stable list; run twice → **byte-identical output**; unit
  tests for dedup, ordering, and slug collisions.

**5. Human-editable plan file + `--dry-run`**
- Files: `scripts/plan_articles.py`
- Change: write the plan to `artifacts/compile-plans/<source-stem>.json`. It is meant to be
  edited by hand — merge two sections, drop a section, retitle. That edit is the whole
  answer to "who decides N".
- Verify: hand-edit the file, re-run the compiler, confirm it honours the edit, not the
  regenerated default.

**6. Pre-flight minting for all N (P4-batch)**
- Files: `scripts/compile_plan.py` (new)
- Change: before **any** write, mint every article's address. Report a table of
  PASS/FAIL per article. If any fails, stop and write nothing.
- Why: discovering "article 4 has no passing hint" after 1–3 are on disk is the failure
  this prevents, and it also protects spent quota (BP-3: checkpointing protects the budget
  you already burned).
- Verify: test where article 3 of 4 fails to mint — assert zero files written.

**7. Two-pass cross-references (P3)**
- Files: `scripts/compile_plan.py`
- Change: pass 1 fixes every slug from the approved plan; pass 2 generates each body with
  the full slug set available, so `--link` targets always resolve.
- Constraint carried forward: generated prose still cannot emit `[[...]]` (rejected at
  validation), so links come only from validated `--link` arguments — safe, and it keeps
  R-1.5 unbreakable by generated text.
- Verify: compile ≥2 articles; assert each page's wikilinks resolve and
  `gen_index.py --check` passes.

**8. Staged batch publish with compensation (P5, BP-2)**
- Files: `scripts/compile_plan.py`
- Change: build all N pages into a **staging directory**; validate and judge them all
  there; only then publish by atomic rename, recording a manifest. On any failure,
  compensate by removing exactly what the manifest says was published.
- This is a saga, not a transaction — the code must say so. Compensation is idempotent and
  retryable, and a compensation that itself fails must surface for human action rather
  than being swallowed.
- Verify: kill the process mid-publish (fault injection in tests); assert `wiki/` is
  byte-identical and `index.md` unchanged.

**9. Checkpoint / resume (BP-3)**
- Files: `scripts/compile_plan.py`
- Change: record per-article completion in the plan file's sidecar. A resumed run skips
  articles already generated and judged.
- Why: N × (2 generations + 1 judge) on a free tier will hit limits mid-run; without this,
  a resume re-burns the quota already spent.
- Verify: interrupt after 2 of 4, resume, assert only 2 remain to do.

**10. Wire into the CLI**
- Files: `scout/cli/commands/`, `docs/CLI_SPEC.md`
- Change: `snpmemory plan-articles` and `snpmemory compile-plan`, both honouring
  `--output json` and the 0/1/2 exit contract.
- Verify: `snpmemory compile-plan --dry-run -o json | jq .` parses.

**11. Full verification, offline then live**
- `ruff check .` · `mypy scout scripts` · `pytest -q` (expect 688 + new, zero regressions)
- Live: `plan-articles` on the paper → review → `compile-plan`
- `verify_addresses.py` → exit 0 for all N
- `verify_groundedness.py` **sequentially** → GROUNDED for all N
- Read two pages by eye; confirm cross-links point somewhere real
- Record the true cost: model calls, wall time, and how often the free tier 429'd

---

### 2026 practice check (verified 2026-08-21)

**BP-1 — stop chasing byte-identical reproducibility; pin the plan instead.**
Bitwise-identical LLM output is not achievable in general: the dominant cause is the
batch-size dependence of reduction kernels, not just floating-point non-associativity, and
batch endpoints guarantee neither idempotency nor result ordering. The working answer is to
**bound variance with temperature, seed, and version pinning, and evaluate for semantic
equivalence rather than byte-exact match.**

Applied: temperature 0 (step 2), a deterministic non-model decomposition (step 4), and an
approved plan file as the pinned artifact (step 5). P2 is answered by *not regenerating the
skeleton*, not by making generation deterministic — which it cannot be.

**BP-2 — a batch of file writes is a saga, not a transaction.**
A compensating transaction is a semantic undo, not a rollback; compensations must be
idempotent and retryable, and some need human intervention once automated retries are
exhausted. Orchestration beats choreography where sequencing and compensation matter.

Applied: staging directory + manifest + explicit compensation (step 8), and the code says
"saga" rather than claiming atomicity `compile_note` already documents it cannot provide.

**BP-3 — concurrency ceilings prevent more 429s than retries clean up.**
A concurrency ceiling just under the endpoint's sustainable rate prevents far more 429s
than any retry strategy; retries need exponential backoff **with jitter** and must honour
`Retry-After`; rejected 429s still consume quota; and checkpoint/resume protects budget
already spent.

Applied: steps 3 and 9. This is not theoretical here — `JUDGE_CONCURRENCY = 3` already
produced a 429 on this free tier, and there is currently **no backoff anywhere in the
codebase**.

**BP-4 — decomposition: prefer structure the document already carries.**
Semantic/section-aware sectioning outperforms naive splitting, and agentic chunking
frameworks exist — but they are aimed at documents with no usable structure. This paper has
31 explicit numbered headings, so the cheap deterministic path dominates: no model call, no
variance, no cost.

Sources:
- [Demystifying Numerical Instability in LLM Inference (arXiv 2606.21023)](https://arxiv.org/html/2606.21023)
- [Bulk LLM jobs: batching, idempotency, QA (2026)](https://www.digitalapplied.com/blog/bulk-llm-job-engineering-batching-idempotency-qa-2026)
- [Saga patterns and distributed consistency in 2026](https://thebackenddevelopers.substack.com/p/event-driven-architecture-saga-patterns)
- [Compensating transaction](https://en.wikipedia.org/wiki/Compensating_transaction)
- [LLM API rate limiting best practices (2026)](https://www.clawpulse.org/blog/llm-api-rate-limiting-best-practices-avoid-429-errors-and-save-40-on-costs)
- [Resilient concurrency and rate limiting for LLM callbacks](https://www.pluralsight.com/labs/codeLabs/resilient-concurrency-and-rate-limiting-for-llm-callbacks)
- [Best chunking strategies for RAG (2026)](https://www.firecrawl.dev/blog/best-chunking-strategies-rag)

---

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Free tier cannot sustain N articles.** Already demonstrated: 2 pages judged concurrently returned exit 2. | Concurrency default 1, backoff with jitter, checkpoint/resume. Report the real 429 count in step 11 rather than hiding it. |
| **Not every section mints.** Some sections are too short or too generic to win rank 1. | Pre-flight (step 6) fails the batch before any write and names the article, so it is a plan edit rather than a half-written vault. |
| **Heading extraction misses or over-splits.** Running headers duplicate; some papers have none. | Dedup by first occurrence; the plan file is hand-editable; a source with no headings falls back to explicit human-authored plan entries. |
| **Staged publish is still not crash-atomic.** A kill between two renames leaves a partial vault. | The manifest makes compensation deterministic, and step 8's fault-injection test proves the ordinary paths. Say plainly it is a saga; do not claim a transaction. |
| **Cost.** N × (2 generations + 1 judge), plus 2 retrievals per attempt. | Pre-flight fails cheap (mint only). Checkpointing prevents re-burning. Step 11 records the measured number. |
| **Cross-links stay shallow** because prose cannot emit `[[...]]`. | Accepted for now; revisit only if reading the compiled vault shows it actually hurts. Recorded, not silently dropped. |

---

### Rollback plan

- New files (`scripts/plan_articles.py`, `scripts/compile_plan.py`, their tests) can be
  deleted without touching the working single-page path.
- Steps 2 and 3 modify shipped files; both are small and independently revertible.
- No migrations, no schema changes. `git revert` restores prior behaviour.
- Vault safety does not depend on the revert: the staged-publish manifest plus the existing
  per-file snapshot/restore mean an aborted batch leaves `wiki/` unchanged.

---

### Deferred, deliberately

- **Inline contextual wikilinks** in generated prose (blocked by the `[[` rejection that
  keeps R-1.5 unbreakable — a deliberate trade).
- **Step 4 (87-reference graph)** and **source-health SH-1..SH-10** remain final tasks.
- **Agent-package fixes** remain near-last, per your ordering.
