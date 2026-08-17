# Superpowers Implementation Plan: Multi-Agent Parallel Stress Testing Campaign (Retrieval & CI/CD)

## Goal
Execute a comprehensive 9-scenario stress test of the SNP Memory System across both Retrieval Performance (Part A) and CI/CD Pipeline Automation (Part B) using parallel subagents connecting strictly to live Docker MCP services (`http://localhost:8765/mcp`, `http://localhost:8080/mcp`, `http://localhost:9000/hooks/wiki-update`, `http://localhost:3000`).

---

## Assumptions
1. All 6 core Docker containers (`snp-gitea`, `snp-litellm`, `snp-postgres`, `snp-basic-memory`, `snp-scout`, `snp-host-sync`) are running healthy.
2. Agents interact with the memory system strictly via MCP protocol over streamable HTTP without reading raw files on disk directly.
3. Vault integrity is preserved: test branches created during Part B will be isolated and cleaned up upon completion.

---

## Plan

### Batch 1: Part A — Live Retrieval & Memory Stress Suite (Parallel Subagents)
- **Step 1: Subagent A1 — Needle in a Haystack (NIAH) Multi-Locator Stress**
  - Query: Target TTFT SLO for Gemini 3.5 Flash and PagedAttention latency under 200ms.
  - Action: Execute `basic-memory.search_notes` $\rightarrow$ `read_note` $\rightarrow$ `Scout.rag_fetch("raw/data/llm_inference_slo_benchmarks.csv", "gemini-3.5-flash")`.
  - Verify: Exact cell metrics (<220ms) and exact page locator citations (`p.2`).
- **Step 2: Subagent A2 — Hard-Negative Distractor Discrimination**
  - Query: Virtual block memory management for GPU KV-cache allocation.
  - Action: Execute `search_notes` with competing distractors (`production-vllm-cluster`, `model-routing-gateway`, `paged-attention-engine`).
  - Verify: Ground-truth `paged-attention-engine` achieves Rank #1 with reciprocal rank score 1.00.
- **Step 3: Subagent A3 — Multi-Hop Incident Response Traversal**
  - Query: Complete failover sequence and routing rules during primary Cloud API outage.
  - Action: Traverse `playbooks/llm-outage-failover` $\rightarrow$ `[[model-routing-gateway]]` $\rightarrow$ `[[production-vllm-cluster]]` and fetch `raw/architecture/model_routing_config.json`.
  - Verify: Full 3-hop graph traversal completed without unnecessary RAG queries for intermediate nodes.
- **Step 4: Subagent A4 — Negative Control, Injection Guard & Token Economy Audit**
  - Query: Non-existent credential file (`raw/secret_keys.txt`) + adversarial instruction injection + token usage measurement.
  - Action: Call `Scout.rag_fetch` with adversarial payload; measure prompt tokens vs. naive context dump.
  - Verify: Return `status: "no_source"`, zero hallucinations, zero instruction execution, and calculate token savings ratio (>85% compression).

---

### Batch 2: Part B — CI/CD & Auto-Healer Pipeline Stress Suite (Parallel Subagents)
- **Step 5: Subagent B1 — Fault Injection & Autonomous Drift Healing Drill**
  - Action: Create test branch `test/drift-probe`, inject a drifted hint into a test page, run `scout/healer.py --ci`, and verify re-minting to `VerifyStatus.PASS` and audit logging in `wiki/log.md`.
  - Verify: Address restored to PASS, 0 broken links, clean git working tree on PR branch.
- **Step 6: Subagent B2 — Adversarial Lint Gate Blocking Drill**
  - Action: Inject corrupted YAML frontmatter / unresolved wikilink in `test/lint-drill`, trigger CI lint gate `scripts/gen_index.py --check`.
  - Verify: Pipeline fails fast with exit code 1, completely blocking the commit step.
- **Step 7: Subagent B3 — Protected Branch Lockdown & Concurrent Webhook Sync**
  - Action:
    1. Attempt `scout/healer.py --ci` on `main` $\rightarrow$ verify refusal with exit code 1.
    2. Dispatch 5 concurrent HMAC-SHA256 signed push webhooks to `http://localhost:9000/hooks/wiki-update`.
  - Verify: Kernel/branch protection confirmed; all 5 webhooks acknowledged with HTTP 200 and processed without lock contention.

---

### Batch 3: Consolidation & Executive Scorecard
- **Step 8: Synthesis & Metric Dashboard**
  - Consolidate results from all subagents into a unified stress-test benchmark scorecard.
  - Clean up temporary test branches (`test/drift-probe`, `test/lint-drill`).
  - Verify final clean vault status with `scripts/gen_index.py --check` and `scripts/verify_addresses.py`.

---

## Risks & Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| **Test Branch Mutation Collisions** | Subagents modifying the same test branch simultaneously | Each subagent operates on a uniquely named, isolated test branch (`test/drift-probe-<id>`). |
| **Flaky Network Timeout During Heavy Concurrency** | Temporary rate limiting or timeout on LiteLLM | Subagents use configured connection retries and explicit timeouts (30s). |
| **Stale State Left in Vault** | Test pages contaminating production wiki | Batch 3 explicitly cleans up temporary branches and verifies master index integrity. |

---

## Rollback Plan
1. Delete any temporary test branches via `git branch -D <branch>`.
2. Re-run `python3 scripts/gen_index.py --check` and `uv run python scripts/verify_addresses.py` to confirm pristine vault state.
