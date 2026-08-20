# Architecture and Documentation Status

This inventory distinguishes current operating instructions from preserved
design history. When documentation conflicts with implementation, the current
code, migrations, Compose files, and `AGENTS.md` operating contract win.

## Active documents

| Document | Authority |
|---|---|
| `AGENTS.md` | Agent query, page, citation, scope, and PR-first contract |
| `README.md` | Current architecture overview and supported commands |
| `docs/runbook.md` | Deployment, readiness, database roles, verification, and incidents |
| `docs/basic-memory-setup.md` | Current containerized basic-memory configuration |
| `docs/DEMO.md` | Current end-to-end demonstration |
| `docs/CONNECT_AGENTS.md` | MCP client wiring; authentication examples are maintained separately |
| `docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md` | **ACTIVE PROPOSAL, not implemented.** Its "Findings" section records verified defects in the current system and is factual; nothing under "Proposed design" exists in the codebase |
| `.agent/` and `packages/snp-agent/` | Active agent instructions; matching portable files must be equivalent |

`wiki/index.md` is generated output, not an authored source. `raw/` is evidence,
not instructions. `artifacts/superpowers/` records audits and executions and is
not an operations manual.

## Current implementation baseline

- LiteLLM routes system model and RAG embedding calls to configured OpenAI,
  Anthropic, or Gemini Cloud APIs.
- PostgreSQL 16 with pgvector is the RAG store. The query path uses
  `rag_app_role`; ingestion uses `rag_ingest_role`; migration administration is
  confined to the migration/provisioning startup path.
- Scout uses request-scoped JWT or static bearer authentication. Development
  mode is loopback-only. Authorization is `Scope.departments`, containing only
  `redteam`, `blueteam`, `ai_eng`, and `infra`; tool input can only narrow it.
- `postgres-migrate` completes before Scout and `sync-job` start.
- Host-sync writes commit-addressed snapshots in the `vault-replica` volume and
  atomically publishes `current`. basic-memory mounts the replica read-only and
  becomes available only after host-sync readiness succeeds.
- Wiki search embeds **in-process inside the basic-memory container** with
  FastEmbed `BAAI/bge-small-en-v1.5` at 384 dimensions
  (`basic-memory/config.json`), and that container is deliberately never given
  the LiteLLM credential. PostgreSQL RAG embeds separately at 1024 dimensions
  through LiteLLM. The two indexes never share a model or a vector dimension.
  The Phase 0 Gate 4 spike concluded the opposite model should be adopted; that
  decision was reversed by what shipped — see "Open decisions" below and the
  banner on `spikes/GATE_RESULTS.md`.
- Address verification enforces two conditions, not a similarity threshold: the
  addressed file must win **rank 1** of its page's department-scoped retrieval,
  and at least **50%** of the hint's content tokens must occur in text that file
  returned (`TOP_RANK` / `GROUNDING_MIN_COVERAGE` in
  `scripts/verify_addresses.py`). `RagChunk.score` carries Reciprocal Rank
  Fusion weights capped near `0.033`, so no score floor is meaningful and none
  is applied.
- `rag_fetch` passes `path=` to the backend, so a mismatched `hint` returns the
  addressed file anyway — the hint governs ranking, never existence. A `loc` is
  a human locator that retrieval does not honor; it is validated at mint time
  (`scripts/mint.py` → `LOC_MISMATCH`) and only advised on at verify time.
- Offline tests run with sockets disabled. Live PostgreSQL and authenticated
  HTTP tests carry the `integration` marker.
- Address verification returns `0` for PASS, `1` for semantic drift/failure,
  and `2` for infrastructure/configuration failure. The CI gate never heals on
  `2`, performs at most one scoped heal pass on `1`, re-verifies, and rolls back
  an unsuccessful heal.

## Historical and reference documents

The following files preserve decisions, rejected designs, test snapshots, or
pre-implementation proposals. Their status banner is authoritative; commands
and topology inside them must not be used to operate the current stack.

- `docs/SESSION_HANDOVER_AND_V2_ROADMAP.md`
- `docs/sprint/IMPLEMENTATION_PLAN.md`
- `docs/rag_failure_analysis.md`
- every Markdown file under `docs/proposal/`
- `docs/HIGH_THROUGHPUT_INFERENCE_BLUEPRINT.md` is a domain/reference report,
  not deployment authority for SNP itself
- `spikes/GATE_RESULTS.md` — the Phase 0 gate ledger. Its Gate 4 conclusion was
  **never implemented**; the file's banner records the reversal and the
  re-measured recall cost
- `artifacts/superpowers/finish.md` — completion handoffs. Its "Needle in a
  Haystack" and token-economy figures were audited on 2026-08-19; see the
  correction banner in that file before quoting any number from it

Historical documents may mention the retired local model and RAG engines,
earlier shared credentials, or old repository mounts because those details are
the subject of the record. Such occurrences are permitted only in clearly
bannered history/reference documents, immutable raw evidence, and audit
artifacts.

## Open decisions

These are recorded, not resolved. Each names the cost of the current default so
that leaving it in place stays a choice rather than an oversight.

### OD-1 — the wiki-search embedding model (opened 2026-08-19, owner decision)

**Question.** Keep FastEmbed `BAAI/bge-small-en-v1.5` @384 for wiki search, or
adopt a multilingual model?

**What ships today.** `bge-small-en-v1.5` @384 — an English-only model. The
Phase 0 Gate 4 spike measured it at recall@1 `0.625` on Vietnamese paraphrases
against `0.812` for a multilingual alternative and concluded it should be
replaced. It was not, and until now no document said so.

**Measured cost of staying, re-probed live 2026-08-19.** The query
`"dual layer memory architecture"` ranks its own exact-title page **5th**
(1.019), behind an unrelated page (1.254). A Vietnamese query returns large
score ties (0.6603 ×3, 0.5619 ×5) — near-random discrimination. Wiki search is
step 1 of the query workflow in `AGENTS.md`, so a page that does not surface is
a page the agent never reads.

**Why this is not a documentation edit.** The model Gate 4 measured ran on a
local model daemon that is no longer part of this stack, and wiki search embeds
in-process inside basic-memory, which is intentionally never given the LiteLLM
credential — so "adopt the Gate 4 winner" is a new integration, not a config
swap. Any change also alters `semantic_embedding_dimensions`, invalidates the
existing SQLite search index, and requires a full basic-memory re-index plus a
re-run of the Gate 4 probe set. That is a runtime behaviour change and an owner
call.

**Options.**
1. Adopt a multilingual FastEmbed model that the pinned `basic-memory==0.22.1`
   image can load in-process, re-index, and re-run the Gate 4 probes.
2. Accept the recall cost explicitly and date the acceptance here, at which
   point Gate 4 is closed as *rejected on integration grounds* rather than
   silently unimplemented.
3. Restrict wiki content and queries to English, making the gate moot.

**Status: OPEN.** No option has been chosen. Neither `basic-memory/config.json`
nor any runtime file was changed while recording this.

## Prohibited claims in active guidance

Active instructions must not describe:

- a local model daemon or the retired monolithic RAG engine as current;
- anonymous Scout access outside loopback development mode;
- runtime use of a PostgreSQL superuser;
- a developer checkout mounted read-write at `/repo`;
- a single embedding model/dimension shared by wiki and RAG;
- DOCX or an unimplemented central ingest REST endpoint as supported;
- caller scope as `roles`, `team`, or a magic `all` authority;
- verifier exit `2` as semantic drift or as permission to mutate;
- direct healer use as the closed-loop CI gate;
- a similarity/relevance score threshold for address verification — no such
  threshold exists in code, and `RagChunk.score` is an RRF weight capped near
  `0.033`, so none can be stated as a similarity;
- a mismatched `hint` returning empty or "dead-ending" — `rag_fetch` pre-filters
  by `path`, so the addressed file is returned regardless;
- the Phase 0 Gate 4 model as deployed for wiki search.

When architecture changes, update implementation and active documents together,
then re-run the stale-claim search described in the review plan. Preserve old
records by changing their banner, not by silently rewriting history.
