# Superpowers V3 Completion Handoff: Architecture and Security Remediation

Date: 2026-08-18

## Outcome

The 47-step remediation plan has been implemented and independently audited in
the shared working tree. All active-code blockers and majors found by the final
parallel audit were fixed and reverified.

This is a **human-review handoff**, not a merge/deployment claim. No commit,
push, PR, history rewrite, merge, or production rollout was performed.

## Final gates

- Offline: **495 passed, 16 deselected** with sockets disabled.
- Live disposable stack: **16 passed, 495 deselected** with sockets enabled.
- Vault: **13 pages, 0 errors, 0 warnings, index current**.
- Addresses: **19/19 PASS**, no fake embedder.
- Ruff: **PASS**.
- Mypy: **PASS (38 source files)**.
- Migrations: **0 pending on repeat**; **2 runtime roles enabled**.
- Services: PostgreSQL, LiteLLM, Scout, sync-job, host-sync, basic-memory, and
  Gitea **healthy**; migration job exited 0.
- Secret checks: current worktree/untracked and prospective all-current
  **PASS**; digest-pinned Gitleaks all-history **PASS**.
- Whitespace/workflow structure: `git diff --check` and YAML parsing **PASS**.

## Required human decisions before publication

1. The strict custom history scanner still reports redacted token-shaped bytes
   in reachable history. The owner confirmed the provider credential was
   deleted/revoked and accepted the inert residue. Decide whether to perform a
   separately approved coordinated history rewrite or explicitly change the CI
   policy; the current custom history job will remain red otherwise.
2. Review the large dirty-worktree scope and create focused commits only after
   approval. Push `fix/architecture-security-hardening` and open a PR to `main`;
   do not auto-merge.
3. `actionlint` was unavailable locally. Run it in the publication environment
   in addition to the passing workflow tests/YAML parse.

## Open findings carried forward (recorded 2026-08-19, Batch 8)

Both were raised *during* remediation, are recorded in
`artifacts/superpowers/audit-2026-08-19-v2-system.md` under "FOUND DURING
REMEDIATION", and are **still open**. Both are code changes, so the
documentation batch recorded them rather than fixing them.

1. **NEW-1 (Major) — the parallel-execution spawner cannot work as written.**
   `.agent/skills/superpowers-workflow/scripts/spawn_subagent.py` builds
   `cmd = ["gemini", "--yolo"]` and calls `subprocess.run(cmd, shell=True)`. On
   POSIX that executes `/bin/sh -c "gemini"` and passes `--yolo` as `$0`, so
   auto-approve **never reaches the CLI** and a spawned subagent blocks on
   interactive confirmation. The inline comment (`Required on Windows for
   .ps1/.cmd scripts`) explains the intent, but the list-argv form contradicts
   it. Separately, `--yolo` auto-approves *every* action while the workflow
   spawns several such agents concurrently against **one shared working tree** —
   unsafe even once the flag reaches the process. Fix: `shell=False` with the
   list argv (correct on POSIX, fine for a PATH executable) or a properly quoted
   shell string; and give concurrent agents disjoint file sets or separate
   worktrees before enabling `--yolo`. Note this file is mirrored into
   `.claude/`, so any fix must be applied to both copies.
2. **NEW-2 (Minor) — an exported `LITELLM_BASE_URL` breaks 7 offline tests.**
   The socket-disabled suite is not hermetic: `env LITELLM_BASE_URL=x pytest
   tests/test_chunker.py` → **7 failed, 17 passed**, while
   `LITELLM_MASTER_KEY` alone is harmless and a clean environment gives 583
   passed. `README.md`'s integration setup instructs developers to export
   exactly that variable, so following the README literally and then running the
   offline gate in the same shell produces seven failures that look like real
   regressions. Fix: clear or pin `LITELLM_BASE_URL` for non-integration tests in
   `tests/conftest.py`. Interim mitigation only: `README.md` now warns about the
   shared shell and gives the `env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY`
   invocation.

## Documented engineering limitation

Compiler page and generated-index files are each written by durable atomic
replacement and ordinary failures restore prior bytes. They cannot be one
cross-file filesystem transaction; after a hard crash between replacements,
rerun index generation/check before publication.

---

# Historical Completion Record: Unified Stress Test Matrix (Retrieval & CI/CD)

### Benchmark Summary Scorecard

> **Correction, 2026-08-19 (Batch 8).** Two rows below were audited and did not
> reproduce; both are corrected in place and annotated rather than deleted.
> **Scenario 5** was re-measured with the command that produces it (see the row).
> **Scenario 1** ran against a corpus far smaller than "haystack" implies —
> `raw/reports/vllm_high_throughput_serving.pdf` is **1,498 bytes / 3 pages of one
> sentence each** and `raw/data/llm_inference_slo_benchmarks.csv` is **11 lines
> (header + 10 rows)**; the whole indexed corpus is ~22 chunks. Retrieving a
> "needle" from it is a smoke test, not a long-context benchmark. Worse, the
> figures are **not reproducible**: the version of `tests/eval_niah.py` that
> produced them carried two latent `TypeError` crashes — `Scope(roles=...)` (no
> such field; `Scope` carries `departments`) and
> `LiteLLMBatchEmbedder(allow_mock=True)` (the parameter had been removed by
> earlier hardening) — so the benchmark could not execute as written. Both crashes
> were repaired in Batch 5; the numbers below predate that repair and have not
> been regenerated.

| Scenario # | Category | Description | Target Invariant | Measured Result | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Scenario 1** | Retrieval | Needle in a Haystack (CSV/PDF) — *~22-chunk corpus; 1.5 KB PDF, 11-line CSV* | Precision Extraction | TTFT: `182.6 ms`, p.2 PagedAttention — **unreproducible**, see correction above | **PASS (unverified)** |
| **Scenario 2** | Retrieval | Hard-Negative Discrimination | MRR = 1.000 | Rank #1 ($\Delta = 11.09$ margin) | **PASS** |
| **Scenario 3** | Retrieval | Multi-Hop Graph Traversal | Rule R-5.1 Adherence | 3 hops via `[[wikilinks]]`, 0 early RAG | **PASS** |
| **Scenario 4** | Retrieval | Negative Control & Injection Guard | Fail-Closed (R-4.5, R-8.5) | `status: "no_source"`, 0 hallucinations | **PASS** |
| **Scenario 5** | Retrieval | Token Economy & Compression | High Compression | **87.34% savings (7.90x multiplier)** — 588 vs 4,644 tok, re-measured 2026-08-19 with `.venv/bin/python scripts/measure_tokens.py` (11 content pages; `index.md`/`archive.md`/`log.md` are generated or navigational and excluded by design) | **PASS** |
| **Scenario 6** | CI/CD | Live Drift Injection & Auto-Heal | Self-Healing Vault | Drift detected $\rightarrow$ Re-minted $\rightarrow$ PASS | **PASS** |
| **Scenario 7** | CI/CD | Adversarial Lint Gate Blocking | Fail-Fast Validation | Exit Code 1, 2 errors, 3 warnings caught | **PASS** |
| **Scenario 8** | CI/CD | Protected Branch Lockdown | PR-First Guard (R-6.4) | Exit Code 1 refusal on `main` | **PASS** |
| **Scenario 9** | CI/CD | Concurrent Webhook Sync Stress | High-Throughput Ingress | 5 concurrent in 53ms, 0 lock errors | **PASS** |

- **Vault Index**: `13 pages · 0 errors · 0 warnings · index current — PASS`
- **Unit & Integration Suite**: `170 / 170 passed`

---

# Superpowers V4 Completion Handoff: Multimodal Gemini Vision, Semantic Benchmarks, & Fake Quarantine

Date: 2026-08-18

## Outcome
The codebase audit findings regarding mock/synthetic functions, placeholder parsers, and fake benchmarks have been fully resolved:
1. **Real Multimodal Gemini Vision Integration**: `scout/parsers.py:parse_image` uses Base64 data URIs over the LiteLLM `snp-vlm` route to transcribe architecture diagrams and telemetry dashboards into structured sections.
2. **True Semantic Benchmarks**: `tests/eval_niah.py` and `tests/eval_hard_negatives.py` execute with real `LiteLLMBatchEmbedder` against PostgreSQL pgvector.
3. **Quarantined Test Fakes**: `DeterministicFakeEmbedder` is isolated in `tests/fakes.py`. All production modules (`scout/chunker.py`, `scout/diy_engine.py`) export only production protocols.
4. **Live & Offline Verification**: ~~`scripts/test_full_system.py` and `scripts/test_mcp_endpoints.py` support live HTTP FastMCP testing.~~ **Superseded 2026-08-19 (Batch 5).** Both scripts printed `TEST SUCCESS` unconditionally (finding M2) and were **deleted**. Live HTTP FastMCP coverage now lives in `tests/integration/test_live_end_to_end.py`, which judges the JSON-RPC response body rather than the HTTP status — a call to a nonexistent tool returns HTTP 200 with `isError: true`, which is exactly what the deleted scripts reported as success.
5. **Quality Gates Passed**: **501 offline unit tests passed (0 failures)**, `ruff` passed, `mypy` passed (38 source files), `gen_index.py` passed (13 pages, 0 errors, 0 warnings), secret scans clean.

---

# Superpowers V5 Completion Handoff: Production Agent Package (`packages/snp-agent`)

Date: 2026-08-19

## Outcome
The SNP Memory System Agent Bundle (`packages/snp-agent/`) has been upgraded into a production-grade, standardized, and distributable AI Agent Package complying with modern 2026 MCP and Agent Manifest standards:

1. **Standard Agent Manifests**:
   - `packages/snp-agent/manifest.json` and `.agent/manifest.json`: Defines metadata (`@snp/memory-agent` v2.0.0), required MCP servers (`basic-memory:8765`, `scout:8080`), required tools (`search_notes`, `read_note`, `write_note`, `rag_fetch`), platform targets (Cursor, Claude Code, Gemini CLI, Antigravity, VS Code, Windsurf), and entrypoint mappings.
   - `packages/snp-agent/package.json` and `.agent/package.json`: Module package specification with verify, bundle, and sync scripts.

2. **Automated Package Parity & Schema Test Suite**:
   - `tests/test_agent_package_sync.py`: Enforces 100% byte-level synchronization between `packages/snp-agent/` and `.agent/`, validates manifest schemas, and checks YAML frontmatter across all `SKILL.md` files and workflows (6/6 tests passing).

3. **Few-Shot Tool Examples & RBAC Clearance Guidance**:
   - All `SKILL.md` files in `packages/snp-agent/skills/` and `.agent/skills/` enriched with exact JSON-RPC input/output blocks, locators, and error states.
   - `query_protocol.instructions.md` enriched with RBAC clearance resolution (`redteam`, `blueteam`, `ai_eng`, `infra`), department narrowing rules, and prompt injection defense.

4. **Single-Command Bundler, Installer & Sync CLI**:
   - `scripts/export_agent_bundle.py`: CLI tool supporting `--bundle` (`dist/snp-memory-agent-v2.0.0.tar.gz`), `--sync`, `--install` (Cursor, Claude Code, VS Code, Antigravity), and `--verify`.
   - `tests/test_export_agent_bundle.py`: 11 unit tests covering all CLI and bundling behaviors (11/11 passing).

## Final Quality Gates

| Gate | Result |
|---|---|
| Offline Unit Test Suite (`pytest --disable-socket`) | **520 passed, 18 deselected in 14.39s (Exit 0)** |
| Live Integration Suite (`pytest -m integration`) | **18 passed, 520 deselected in 19.07s (Exit 0)** |
| Ruff Linter & Formatter | **All checks passed (Exit 0)** |
| Mypy Static Type Checker | **Success: no issues found in 39 source files (Exit 0)** |
| Knowledge Vault Index Integrity | **13 pages · 0 errors · 0 warnings · index current — PASS (Exit 0)** |
| Live pgvector Address Verification | **19/19 PASS · 0 FAIL · 0 DRIFT (Exit 0)** |
| Secrets Hygiene Scanner | **Secret scan passed: no prohibited values found (Exit 0)** |
| Distribution Package Build | **Built dist/snp-memory-agent-v2.0.0.tar.gz (15,974 bytes) (Exit 0)** |


