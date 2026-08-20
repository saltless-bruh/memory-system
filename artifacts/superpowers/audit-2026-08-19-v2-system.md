# V2 System Audit — SNP Memory System

**Date:** 2026-08-19 · **Branch:** `fix/architecture-security-hardening` · **HEAD:** `92f5b42`
**Method:** static code read + live probing of the running stack (`snp-memory`, 8 services) +
full gate execution. Nothing in the working tree was modified.

## Verdict

**This is a real, running, working system — not a mock.** The infrastructure, the security
boundary, and the retrieval path are genuine and independently verifiable. What is not real is a
layer of *verification theatre* on top of it: an address gate that passes gibberish, two scripts
that print "TEST SUCCESS" unconditionally, a benchmark that can never execute, silent fabrication
in image ingestion, and several authoritative doc claims the code contradicts. Separately, two of
three model routes are dead right now, which takes the entire authoring half of the product
offline while every container still reports healthy.

---

## Verified real (evidence)

| Claim | Evidence |
|---|---|
| Stack live | 8 services up/healthy; `postgres-migrate` exited 0; real named volumes |
| Tests | 503 offline pass (sockets disabled) + 18 integration pass against live services |
| Tests fail closed | Unsetting prerequisites → 18 **errors**, not skips |
| Static quality | `ruff` clean · `mypy` strict clean (38 files) · `gen_index.py --check` 13 pages, 0 errors |
| Real RAG corpus | 10 docs, 22 chunks, 22/22 embedded at **1024 dims** via real `gemini-embedding-001` |
| RLS enforced | No clearance → **0 rows**; `rag_app_role` write → `permission denied`; `FORCE ROW LEVEL SECURITY` |
| Scout authz | 401 unauthenticated · 401 bad token · scope expansion → `requested departments exceed authenticated scope` · `all` → `non-canonical values` |
| Scout retrieval | Live MCP `rag_fetch` → real verbatim chunks + citations; exactly one tool exposed |
| host-sync | Unsigned → 403 · bad HMAC → 403 · valid HMAC → 202 · commit-addressed snapshot + atomic `current` symlink |
| basic-memory | Live; `permalink: None` confirms `disable_permalinks`; vault never mutated |
| No fake fallbacks | `serve.py` refuses non-pgvector; `verify_addresses.main` defaults to *no backend* → exit 2 |
| Addresses | 19/19 PASS live (but see B1 for what that actually proves) |
| Secrets | `.secrets/` 0700, files 0600, gitignored, mounted as Docker secrets |

---

## BLOCKERS

### B1 — The address verifier passes pure gibberish
`scripts/verify_addresses.py:56-60` retrieves top-5 **globally** and returns PASS if the addressed
file appears *anywhere* in that top-5. No score threshold, no rank requirement. The corpus is 22
chunks, so k=5 is ~23% of everything.

Measured against `raw/reports/vllm_high_throughput_serving.pdf`:

| Hint | Result |
|---|---|
| real minted hint | PASS |
| vocabulary describing a *different* file | **PASS** |
| unrelated domain (Kerberoasting / Active Directory) | **PASS** |
| `"zzqq banana marmalade unicycle wobble 8842"` | **PASS** |
| `"the"` | DRIFT |

Consequences: "19/19 PASS" is near-zero signal. `mint.py:mint_address` calls the same
`verify_address`, so "verify-PASS by construction" is circular — the first candidate phrase almost
always wins. Injecting real drift produced **0 proposed heals**, so the auto-healer cannot fire.
`FetchStatus.FAIL` is structurally unreachable: dense search always returns rows.

**Fix:** require the target as top-1 (or top-N with N≪corpus), add a real score floor on a
normalized similarity (not RRF), and make `k` relative to corpus size.

### B2 — Two of three model routes are dead; Nhịp B is non-functional
`snp-llm` and `snp-vlm` both return HTTP 404:
`"models/gemini-2.5-flash is no longer available to new users … use models/gemini-3.6-flash"`.
`.env` sets `LITELLM_LLM_MODEL=LITELLM_VLM_MODEL=gemini/gemini-2.5-flash`. Only `snp-embed`
(`gemini-embedding-001`) works — which is why retrieval looks fine.

- `compile_note.generate_model_data()` → `CompileNoteError: Model gateway request or response decoding failed`
- So `/snp-compile`, `compile_note.py`, and VLM ingestion are all broken.
- **Every container still reports healthy**: the compose healthcheck probes LiteLLM
  `/health/liveliness` only, which is up regardless of whether any model resolves.

**Fix:** repoint to a live model; add a model-route probe to the healthcheck or a startup gate.

### B3 — Silent fabrication in image ingestion
`scout/parsers.py:parse_image` catches `ParserError` and substitutes a synthetic description.
Live DB proves it is happening — both images stored the stub, not a transcription:

```
Visual Image Asset: Inference Dashboard (inference_dashboard.png).
Format: PNG, Size: 155 bytes. Stored at raw/images/... for multimodal system reference.
```

That text is embedded, indexed, addressed by `wiki/concepts/model-routing-gateway.md`, and served
to agents as evidence. `ParserError`'s own docstring says it exists so a source is never parsed
"without fabricating content" — and then the fabrication path is taken silently, with no log.
`artifacts/superpowers/finish.md` claims "Real Multimodal Gemini Vision Integration" as delivered.

**Fix:** let `ParserError` propagate (fail the ingest), or persist an explicit
`status: vlm_unavailable` that retrieval refuses to cite.

---

## MAJORS

### M1 — Department RLS is inert: every document is `{all}`
`scout/sync_job.py:115` hardcodes `allowed_depts: tuple[str, ...] = ("all",)`; `scout/ingest.py:300`
defaults to `"all"`; **no env var, no path→department mapping exists anywhere**, and compose passes
none to `sync-job`. Live: `SELECT DISTINCT allowed_depts FROM rag_documents` → `{all}` only.

The RLS machinery is real and correct, but with a uniformly public corpus it can only distinguish
*authenticated* from *unauthenticated*. `docs/DEMO.md`'s "token lacking the page department must
not expose the source" cannot be demonstrated on this deployment.

### M2 — Two scripts print "TEST SUCCESS" unconditionally
- `scripts/test_full_system.py` — **zero assertions**; prints
  `TEST SUCCESS: Full Wiki -> RAG Data Vault flow verified!` regardless of outcome; uses
  `FakeEmbedder(dims=512)` for the wiki engine *even with `--live`*; seeds fabricated TCP/IP chunks
  for `raw/rfcs/rfc793-tcp.md`, a path that does not exist in this repo.
- `scripts/test_mcp_endpoints.py` — `--live` treats **any HTTP 200 as success without reading the
  body** (an MCP JSON-RPC error is HTTP 200); on failure it silently falls back to an in-memory
  `FakeRagBackend` and still prints `TEST SUCCESS: … verified!`; hardcoded fallback token
  `"scout-dev-token"`.

Both are cited in `finish.md` as verification evidence.

### M3 — The documented 0.70 similarity threshold does not exist and cannot
`.agent/workflows/snp-verify.md:15` and `packages/snp-agent/workflows/snp-verify.md:15` — both
**active** agent instructions — tell agents to ensure every hint resolves "with score ≥ 0.70".
No threshold exists in code. Live citation scores are RRF values **0.031–0.033**; RRF with k=60
caps near 0.033, so 0.70 is unreachable by construction.

### M4 — AGENTS.md §5 describes the opposite of shipped behavior
> "a hint written from *your* vocabulary instead of indexed embeddings returns empty **silently** —
> the path exists, lint passes, but retrieval dead-ends."

Live: nonsense hint on a real file → `status=ok`, 5 chunks (the whole document). Because
`rag_fetch` passes `path=` to the backend, a bad hint returns the *entire file*, never empty.

### M5 — Gate 4's binding decision was silently reversed
`spikes/GATE_RESULTS.md`: "✅ **SWITCH TO bge-m3** (FastEmbed default inadequate for VN)",
recall@1 0.625 → 0.812, paraphrase@3 0.875 → 1.000. `docs/sprint/IMPLEMENTATION_PLAN.md:50` marks
T-1.4 "✅ **verified** — bge-m3 via LiteLLM (settled)". Shipped `basic-memory/config.json` uses
`BAAI/bge-small-en-v1.5` @384 — **exactly the model the gate rejected**.

Measured live consequence:
- `"dual layer memory architecture"` ranks its own exact-title page **5th** (1.019), behind
  "Adversarial Prompt Injection Incident Response" (1.254).
- The Vietnamese query produces large score ties (0.6603 ×3, 0.5619 ×5) — near-random discrimination.

`GATE_RESULTS.md` appears in neither the active nor historical inventory of `ARCHITECTURE_STATUS.md`.

### M6 — The CI security gate is permanently red on the project's own placeholders
`.gitea/workflows/security.yaml:37` runs `scan_secrets.py --all-current --history` → **exit 1**.
Findings are `sk-local-dev-…` placeholders (verified shape: prefix `sk-loc`, 24 chars — not a real
credential) in `artifacts/multimodal_system_analysis.md`, `tests/test_secrets_hygiene.py`, and
`tests/eval_ragas.py` in history. `PLACEHOLDER_VALUES` already contains `^sk-local-dev-[a-z0-9-]+$`,
but `PLACEHOLDER_PATHS` limits the exemption to `.env.example` / `docker-compose.yml`.
`--worktree --untracked` does pass, so `finish.md`'s "prospective all-current PASS" is now false.

### M7 — No groundedness gate; the reference vault contains ungrounded claims
`wiki/entities/vllm-inference-cluster.md` asserts "NVIDIA A100/H100 GPUs" — **zero occurrences of
A100 or H100 anywhere in `raw/`** (re-verified by full recursive grep) — and "p99 latency under
500ms", which the page did not cite a source for.

> **CORRECTION 2026-08-19 — half of this finding was my error.** I wrote that the p99 claim's only
> support was "a CSV of *cloud API* models (`gemini-3.5-flash`)". That was based on reading only the
> first five lines of `raw/data/llm_inference_slo_benchmarks.csv`. The full file **does** contain
> `llama-3.3-70b-vllm` rows — p99 `310.2` / `480.0` / `790.4` ms at concurrency 1 / 16 / 64, with
> real `vram_usage_gb` of 38.5 / 62.4 / 88.2. The claim was therefore *supportable*; the page simply
> failed to cite the source and overstated it (sub-500 ms holds only to concurrency 16, not
> generally). The **A100/H100 half stands** — that was a full-tree grep. Lesson: `head -5` is not
> evidence about a file's contents. It passes lint and address
verification because **no gate checks body-vs-source faithfulness**. This is precisely the
hallucination class the architecture exists to prevent, sitting in the reference vault.

### M8 — `tests/eval_ragas.py` is a non-functional benchmark
- Top-level `from datasets import Dataset`, unguarded; `datasets` is not a declared dependency →
  ImportError on a clean install.
- `backend.retrieve(hint=query, k=2)` passes **no `scope`** → `_resolve_depts(None)` returns `""` →
  RLS fails closed → always zero chunks → always short-circuits.
- The "dataset" is one hardcoded synthetic Q/A pair with a hand-written `answer` and `ground_truth`;
  it never evaluates the system's own output.
- `OpenAIEmbeddings(model="gemini/gemini-embedding-2")` — not a **configured LiteLLM route**
  (only `snp-embed` / `snp-llm` / `snp-vlm` exist in `config/litellm/config.yaml`).
  *Correction 2026-08-19:* an earlier draft of this audit called `gemini-embedding-2` a
  nonexistent model. It is real — it appears in this key's model list. Only the routing
  criticism stands.
- `os.environ["OPENAI_API_BASE"] = "http://localhost:4000"` hardcoded and missing `/v1`.

---

## FOUND DURING REMEDIATION

### NEW-1 (Major) — the parallel-execution spawner cannot work as written
`.agent/skills/superpowers-workflow/scripts/spawn_subagent.py:103-130` builds
`cmd = ["gemini", "--yolo"]` and calls `subprocess.run(cmd, shell=True)`. On POSIX that runs
`/bin/sh -c "gemini"` and passes `--yolo` as `$0`, so **auto-approve never reaches the CLI** and a
spawned subagent blocks waiting for interactive confirmation. The comment on the line
(`shell=True,  # Required on Windows for .ps1/.cmd scripts`) explains the intent but the argv form
is incompatible with it.

Second problem: `--yolo` auto-approves *every* action, and the workflow spawns several such agents
concurrently against **one shared working tree** — here carrying 183 uncommitted changes. Even with
the flag fixed, that is unsafe without per-agent isolation.

**Fix:** use `shell=False` with the list argv (correct on POSIX and fine for a PATH executable), or
pass a properly quoted shell string; and give concurrent agents disjoint file sets or separate
worktrees before enabling `--yolo`.

### NEW-2 (Minor) — an exported `LITELLM_BASE_URL` breaks 7 offline unit tests

The socket-disabled suite is not hermetic. `README.md:153` tells developers to
`export LITELLM_BASE_URL=http://127.0.0.1:4000/v1` before integration runs; doing so and then
running the offline suite in the same shell yields seven spurious failures in `tests/test_chunker.py`
that look like real regressions.

```
env LITELLM_BASE_URL=x   pytest tests/test_chunker.py  ->  7 failed, 17 passed
env LITELLM_MASTER_KEY=x pytest tests/test_chunker.py  -> 24 passed
clean env, whole suite                                 -> 583 passed
```

**Fix:** clear or pin `LITELLM_BASE_URL` for non-integration tests in `tests/conftest.py`.

---

## MINORS

- **m1** Chunk `loc` is inherited from the parent section on split. The CSV yields one section
  `"Rows 1-10"` split into 3 chunks — all three labeled `"Rows 1-10"`. A citation can name rows the
  quoted text is not from.
- **m2** `Address.loc` is decorative. Address `loc: "Section System Architecture Overview"` returned
  a top chunk with `loc=Section Introduction`; `rag_fetch` uses `c.loc or address.loc`, so the
  address locator is never validated or used to rank/filter.
- **m3** `finish.md`'s "94.31% savings (17.58x)" is not reproducible. `scripts/measure_tokens.py`
  today: 583 vs 4639 tok = **87.4% / 7.96×**. It also reports "11 pages" against a 13-page vault.
- **m4** The "Needle in a Haystack" scorecard overstates its corpus: the PDF is 1.5 KB / 3 pages of
  one sentence each; the CSV is 11 rows; the whole corpus is 22 chunks.
- **m5** `.claude/` has drifted from `.agent/` and is **untracked**. 4 files differ, including
  `instructions/query_protocol.instructions.md` and 3 `SKILL.md` files; `.claude/` holds the older,
  thinner contract — and Claude Code reads `.claude/`.
- **m6** `.agent` ↔ `packages/snp-agent` equivalence is asserted in README and ARCHITECTURE_STATUS
  but only partially tested (`test_agent_package.py:152` covers the 8 skills; `package.json` differs
  and is unchecked).
- **m7** Healer robustness: `apply_heal_edit` indexes `sources[]` by regex-matching
  `^\s*-\s+path:` across the *whole file*, so a body code block containing frontmatter YAML (as
  AGENTS.md itself demonstrates) shifts the index — it fails closed but refuses legitimate heals.
  It also assumes `path:` is the first key of each entry. `append_heal_to_log` creates a
  frontmatter-less `wiki/log.md` when missing, which would immediately fail `gen_index.py --check`.
- **m8** `tests/eval_niah.py` prints "Needle retrieved successfully" even when the status marker is ❌.
- **m9** `docker-compose.yml:6` comment says `postgres … internal-only`, but the service now
  publishes `127.0.0.1:${POSTGRES_PORT:-5432}:5432`. Loopback-only, so low risk — the comment is wrong.
- **m10** Static bearer tokens never expire (`StaticTokenVerifier` sets no `expires_at`); rotation
  means editing the file and restarting. Inherent to the mode, but undocumented.
- **m11** Stray empty `~/` directory in the repo root; `.agents/` and `.codex/` are also empty.

---

## FUTURE WORK — no handling path for unusable source data

*Raised by the owner, 2026-08-19, from the `inference_dashboard.png` case.*

The system can now **detect** that a source is unusable, but it has **no feature that acts on that
knowledge.** Detection and handling are not the same capability, and only the first exists.

What detection already produces:
- `scout/parsers.py:parse_image` sets `metadata["vlm_status"]` (`ok` / `unavailable` /
  `unconfigured`) and `metadata["vlm_error"]`, and logs a warning.
- The model itself gives a precise, actionable diagnosis — Gemini returned
  `400 INVALID_ARGUMENT — "Unable to process input image"` for the 155-byte placeholder PNG.
- Ingestion reports `purged_empty` for that file.

What is missing:
1. **Nothing aggregates or surfaces it.** `vlm_status` is written into chunk metadata for documents
   that *have* chunks; a document with zero chunks is deleted, so its failure metadata is discarded
   with it. The only trace is a log line in a container nobody reads. There is no report, no queue,
   no exit code, no CI gate that says "3 of your 12 sources could not be read."
2. **No quarantine.** The file stays in `raw/` and is re-parsed, re-sent to the VLM, and re-failed on
   every sync — paying the API call each time — with no backoff and no "known bad" marker.
3. **The status taxonomy has no word for it.** `verify_addresses.py` classifies PASS / DRIFT / FAIL,
   where DRIFT means "retrieved a different file" and FAIL means "retrieved nothing". An unreadable
   source is neither: the address is not stale and the hint is not wrong — *the evidence does not
   exist*. It currently reports as DRIFT, which actively misdiagnoses the problem and sends an
   operator to `/snp-heal`, which cannot possibly fix it (there are no chunks to re-mint against).
   This deserves a distinct status, e.g. `NO_EVIDENCE`, that routes to "fix the source" rather than
   "fix the address".
4. **The wiki side is blind.** A page can cite a source that yields nothing and still pass
   `gen_index.py --check`, because the linter only verifies that `sources[].path` exists **on disk**
   — not that it produced any indexed evidence. A zero-byte or corrupt file passes the lint.
5. **No operator affordance.** There is no command answering "which sources are currently unusable,
   and why?" — the one question this failure mode raises.

Suggested shape for a future batch:
- Persist ingest outcomes (including failures) to a table or a generated report rather than only to
  logs, so failures survive the document being purged.
- Add a `NO_EVIDENCE` verification status distinct from DRIFT/FAIL, and route it to source
  remediation rather than address healing.
- Extend the vault linter to check that each `sources[].path` resolves to at least one indexed chunk,
  not merely that the file exists on disk.
- Add a `--report` mode listing unusable sources with the upstream diagnosis attached.

Owner's decision for the immediate case: **replace the flawed test data** rather than work around it.

---

## Recommended order

1. **B2** — repoint `LITELLM_LLM_MODEL` / `LITELLM_VLM_MODEL` to a live model; add a model-route
   probe so "healthy" means the models resolve.
2. **B3** — stop swallowing `ParserError` in `parse_image`.
3. **B1** — give the address gate a real pass criterion (rank + normalized score floor).
4. **M1** — add a path→department mapping for `sync-job`, or state plainly that the corpus is public.
5. **M2 / M8** — delete or rewrite the three scripts that report success without verifying anything.
6. **M3 / M4 / M5** — correct the active agent instructions and AGENTS.md; re-banner `GATE_RESULTS.md`.
7. **M6** — extend the placeholder exemption, or purge placeholders from committed content.
8. **M7** — add a faithfulness check to the merge gate (this is what `eval_ragas.py` was reaching for).
9. **NEW-1** — fix `spawn_subagent.py`'s `shell=True` + list-argv contradiction, and give concurrent
   agents disjoint file sets or separate worktrees before `--yolo` is enabled. Still **open**: it is
   code, and Batch 8 (documentation) deliberately did not touch it.
10. **NEW-2** — make the deterministic suite hermetic by clearing or pinning `LITELLM_BASE_URL` for
    non-integration tests in `tests/conftest.py`. Still **open**; `README.md` now carries an interim
    warning and the `env -u …` invocation, which is documentation, not a fix.
