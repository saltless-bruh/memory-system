# SNP Memory System — Implementation Plan (Phase 0 → Phase 1)

> Coordination dashboard. Adapted from the `create-implementation-plan`
> workflow to a local file — this project's "tickets" are the `T-x.y`
> checkboxes in [tasks.md](../../specs/tasks.md); overview docs are local
> Markdown. Traceability to [requirements.md](../../specs/requirements.md)
> and [design.md](../../specs/design.md) is preserved via R-x.y mappings.
>
> **Repo layout:** specs in `specs/`, proposal + sprint docs in `docs/`,
> code in `scripts/` `spikes/` `config/`, vault in `wiki/` + `raw/`.

## Summary

| Metric | Value |
|---|---|
| **Source of truth** | [Proposal_SNP_Memory_System_v2.md](../proposal/Proposal_SNP_Memory_System_v2.md) (v2.2) |
| **Status** | **Phase 0 COMPLETE** (all 4 gates concluded); Phase 1 done except T-1.5 live demo (Gitea-gated) |
| **Ordering principle** | Spike-before-code — resolve gates before committing to the primary engine (basic-memory) |
| **Gate 3 (AGPL)** | ✅ ALLOWED (provisional, build-to-evaluate, per lead) — basic-memory branch is a go |
| **basic-memory** | ✅ installed + running on the vault; required config captured in [basic-memory-setup.md](../basic-memory-setup.md) |

## Environment (verified 2026-07-20)

| Dependency | State |
|---|---|
| Python 3.14.0 | ✅ compatible with all core deps |
| Docker 29.1.3 / Compose v5 | ✅ **daemon up**; `git` + `litellm` containers healthy |
| Ollama 0.32.1 | ✅ installed + running; models `bge-m3`, `qwen2.5:7b-instruct`, `qwen2.5vl:7b` pulled (→ `D:\Ollama\models`) |
| fastembed 0.8.0 | ✅ installed (Gate 4 comparison backend) |
| basic-memory | ❌ not installed — **gated on Gate 3 (AGPL decision)** |

## Completion status

| Task | Phase | State | Evidence |
|---|---|---|---|
| T-0.1 Repo + Compose | 0 | ✅ **verified** | containers healthy; R-1.2 tree present. Push/clone pending Gitea admin bootstrap (human account step) |
| T-0.2 LiteLLM routing | 0 | ✅ **verified** | embed+LLM+VLM answer through the chokepoint on local Ollama; fixed `${VAR}`→`os.environ/` bug |
| T-0.3 Gate 3 AGPL | 0 | ✅ **ALLOWED** (provisional) | [DECISION_MEMO.md](../../spikes/gate3_agpl_license/DECISION_MEMO.md) |
| T-0.4 basic-memory + vault | 0 | ✅ **verified** | 7 entities / 13 relations / 62 chunks; search returns results |
| T-0.5 Gate 1 sync | 0 | ✅ **PASS — SQLite for V1** | watch + doctor keep index consistent under external Git edits/churn |
| T-0.6 Gate 2 passthrough | 0 | ✅ **PASS via config** | 0 file writes with `disable_permalinks`+`ensure_frontmatter_on_sync:false` |
| T-0.7 Gate 4 recall | 0 | ✅ **concluded** | bge-m3 @3=1.0 vs FastEmbed-EN 0.81. [GATE_RESULTS.md](../../spikes/GATE_RESULTS.md) |
| T-1.1 AGENTS.md | 1 | ✅ **done** | [AGENTS.md](../../AGENTS.md) |
| T-1.2 gen_index.py | 1 | ✅ **verified** | `--check` PASS; catches 3 planted faults |
| T-1.3 basic-memory MCP → IDE | 1 | ✅ **server runs** | `bm mcp` streamable-http :8765; search/read work. IDE client config is the member's step |
| T-1.4 wiki-search embedding | 1 | ✅ **verified** | **bge-m3 via LiteLLM** (settled) + index.md excluded + threshold 0.35 → 6/6 VN paraphrases hit @1 |
| T-1.5 PR-first write path | 1 | ◑ **built + dry-run verified** | `scripts/propose_page.py` (branch-off-main, lint gate, no auto-merge). Live PR/Web-UI demo needs Gitea admin bootstrap |

✅ verified · ◑ partial · ⏳ pending infra/decision · ⛔ blocked

## What remains

- **T-1.5 live demo (operator-gated):** bootstrap the Gitea admin account +
  push the repo, then `scripts/propose_page.py --push` to see a PR appear
  and confirm the Web-UI edit path. Mechanism is built + dry-run-verified;
  only the live Gitea proof is outstanding.
- **Operator confirmations still open:** T-0.1 Gitea push/clone, T-0.2
  physical no-egress cut.
- **Next:** Phase 2 (RAG-Anything + Scout).

**Embedding — SETTLED:** bge-m3 via LiteLLM (`provider=litellm`,
`ollama/bge-m3`, dims 1024); 6/6 VN paraphrases @1; unified with RAG;
refines R-8.2 (local via LiteLLM). See [basic-memory-setup.md](../basic-memory-setup.md).

## Gate → branch table

| Gate | Task | If it FAILS |
|---|---|---|
| AGPL policy | T-0.3 | Scout-DIY becomes primary; drop the basic-memory branch |
| Git↔index sync | T-0.5 | Try Postgres backend; if still drifts → Scout-DIY primary |
| sources[] passthrough | T-0.6 | Move address to body-block/sidecar; contract unchanged |
| Vietnamese recall | T-0.7 | ✅ FIRED → wiki-search unified on bge-m3 (no design change) |

## Key artifacts

| Path | Purpose |
|---|---|
| [AGENTS.md](../../AGENTS.md) | Agent operating contract: schema, injection guard, mint rule, PR-first (T-1.1) |
| [scripts/gen_index.py](../../scripts/gen_index.py) | Deterministic index + linter; `--check`/`--stdout` (T-1.2) |
| [spikes/_lib/vault.py](../../spikes/_lib/vault.py) | Shared parse/lint/tree-check |
| [config/litellm/config.yaml](../../config/litellm/config.yaml) | snp-embed/llm/vlm → local Ollama, no cloud fallback |
| [spikes/GATE_RESULTS.md](../../spikes/GATE_RESULTS.md) | Gate ledger (Gate 4 concluded) |
| [spikes/README.md](../../spikes/README.md) | Operator verification runbook |

## Next steps

1. **Resolve Gate 3 (AGPL)** — the sole blocker; see the decision memo.
2. If ALLOWED: install basic-memory → verify T-0.4 → run Gates 1 & 2 →
   then Phase 1 T-1.3 (MCP to IDE), T-1.4 (point embedding at bge-m3),
   T-1.5 (PR-first write path).
3. If DENIED: pivot to Scout-DIY primary (T-2.4); T-1.x basic-memory tasks
   drop.
4. Operator confirmation still open on T-0.1 (Gitea push/clone) and T-0.2
   (physical no-egress cut).
