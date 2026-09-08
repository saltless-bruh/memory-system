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
| `docs/DEMO.md` | Current end-to-end demonstration |
| `docs/CONNECT_AGENTS.md` | MCP client wiring; authentication examples are maintained separately |
| `docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md` | **ACTIVE PROPOSAL, not implemented.** Its "Findings" section records verified defects in the current system and is factual; nothing under "Proposed design" exists in the codebase |
| `.agent/` and `packages/snp-agent/` | Active agent instructions. `packages/snp-agent/` is an Agent Plugins 1.0.0 plugin; matching portable files must be equivalent, and the `superpowers-*` layer is deliberately repo-local and, since 2026-08-27, absent from `.claude/` as well — Claude Code uses `unlazy` |

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
  atomically publishes `current`. Scout mounts the replica read-only and
  becomes available only after host-sync readiness succeeds; `sync-job` mounts
  the same replica and re-indexes it on change. No separate wiki-engine
  container reads it.
- Wiki search and PostgreSQL RAG now share **one** embedding index: both are
  produced through LiteLLM at 1024 dimensions (`scout/chunker.py`,
  `scout/ingest.py`), distinguished only by a stored corpus tier
  (`scout/wiki_ingest.py::WIKI_CORPUS`), never by a separate model or vector
  space. There is no in-process FastEmbed model. `basic-memory` is no longer
  part of this system (the directory remains on disk but unbuilt; no Compose
  service or CI workflow instantiates it, though its `requirements.lock`
  remains tracked as a release-manifest input), and the 384-dimension model
  the Phase 0 Gate 4 spike evaluated is not part of the deployed system — see
  "Open decisions" below and the banner on `spikes/GATE_RESULTS.md`.
- Address verification enforces two conditions, not a similarity threshold: the
  addressed file must win **rank 1** of its page's department-scoped retrieval,
  and at least **50%** of the hint's content tokens must occur in text that file
  returned (`TOP_RANK` / `GROUNDING_MIN_COVERAGE` in
  `scripts/verify_addresses.py`). `RagChunk.score` carries Reciprocal Rank
  Fusion weights capped near `0.033`, so no score floor is meaningful and none
  is applied.
- `rag_fetch` is no longer an agent-facing tool — `scout/mcp_server.py` exposes
  only `wiki_search` and `wiki_read` — but the same engine call still backs
  `scripts/verify_addresses.py` and the `scout rag` CLI. It passes `path=` to
  the backend, so a mismatched `hint` returns the addressed file anyway: the
  hint governs ranking, never existence. A `loc` is a human locator that
  retrieval does not honor; it is validated at mint time (`scripts/mint.py` →
  `LOC_MISMATCH`) and only advised on at verify time.
- `scripts/compile_note.py` generates a page body from the passages the page's
  minted address retrieves (`verify_groundedness.collect_context`, `k=20`, page
  department scope) and judges it against those same passages before writing.
  Generation and judging therefore read one corpus. A page the judge cannot
  ground is not written; `--skip-groundedness` bypasses the check and says so.
  Body prose containing `##`, `[[`, `---`, or control characters is rejected at
  validation, so generated text can never break heading order or create an
  unvalidated wikilink (R-1.5).
- Two MCP surfaces exist and they are not interchangeable.
  **`scout`** is the deployed one: a container on Streamable HTTP with exactly two
  tools, `wiki_search` and `wiki_read`, behind request-scoped JWT/static
  authentication. It remains the only door into the wiki and the RAG index.
  **`snpmemory mcp`** is a local **stdio** server exposing this repository's own
  operations (`verify`, `plan_articles`, `compile_plan`, `compile_status`), generated
  from the command declarations in `scout/cli/declarations.py`.
  It carries **exactly the authority of the user who launches it** — no token, no
  scope check, no listener. That is why it is stdio-only. Serving it over HTTP would
  require an OAuth 2.1 design with audience validation; a server that accepts a token
  it was not issued is the confused-deputy failure the MCP guidance exists to prevent.
  Tool effects come from each command's declared `Effect`, so `readOnlyHint` and
  `destructiveHint` cannot disagree with what a command actually does. A command with
  no exposure decision in `scout/cli/mcp_policy.py` fails the test suite.
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
- `docs/basic-memory-setup.md` — describes `basic-memory`, removed in v3
  along with the wiki-only FastEmbed model it ran in-process. Wiki search now
  shares Scout's PostgreSQL/pgvector index, embedded through LiteLLM at 1024
  dimensions; this document's own banner still reads "Current," which is
  itself dated and should not be trusted over this inventory
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

### OD-1 — the wiki-search embedding model (opened 2026-08-19, closed 2026-09-08 as moot)

**Question, as originally opened.** Keep FastEmbed `BAAI/bge-small-en-v1.5`
@384 for wiki search, or adopt a multilingual model?

**Resolution.** Moot. `basic-memory` is no longer part of this system. Wiki
search no longer embeds in-process at all: `wiki_search` now queries the same
PostgreSQL/pgvector index as source retrieval, embedded through LiteLLM at
1024 dimensions
(`scout/diy_engine.py`, `scout/chunker.py`). No owner chose an option below —
the integration boundary that made this a decision was removed by the
architecture change instead.

**Historical record, prior to v3.** `bge-small-en-v1.5` @384 shipped despite
the Phase 0 Gate 4 spike measuring it at recall@1 `0.625` on Vietnamese
paraphrases against `0.812` for a multilingual alternative and concluding it
should be replaced. Re-probed live on 2026-08-19, while it was still deployed:
the query `"dual layer memory architecture"` ranked its own exact-title page
**5th** (1.019), behind an unrelated page (1.254), and a Vietnamese query
returned large score ties (0.6603 ×3, 0.5619 ×5) — near-random discrimination.
That measurement is retained for the audit trail; it does not describe the
current index, which is not English-restricted by a local model.

**Status: CLOSED (moot).** A separate wiki-only embedding path does not exist
today, so there is nothing left to decide. The multilingual-adoption question
would resurface only if a future change reintroduced one.

### OD-2 — the auto-healer's push credential (opened 2026-08-26, owner decision)

**The mitigation first proposed here would not have mitigated anything.** The
plan called for job-level `permissions:`, which governs the **ambient Actions
token**. Every privileged operation in `auto-healer.yaml` uses
`secrets.BOT_TOKEN` instead:

| Line | Job | Use |
| --- | --- | --- |
| 57 | `pr-heal` | `actions/checkout` of the PR head, `persist-credentials: true` |
| 108 | `pr-heal` | `git push origin "HEAD:$PR_HEAD_REF"` — **pushes to the contributor's branch** |
| 127, 132 | `scheduled-sweep` | checkout of `main`, and `GITHUB_TOKEN` for the environment |
| 175, 182 | `scheduled-sweep` | `POST /api/v1/repos/{owner}/{repo}/pulls` — **creates a pull request** |

**Both jobs push.** An earlier description of the PR job as read-and-comment was
simply wrong. The real asymmetry is narrower: only `scheduled-sweep` creates pull
requests through the API.

| Job | Needs | Does not need |
| --- | --- | --- |
| `pr-heal` | clone; push to an existing branch | PR creation, issue write, org or admin anything |
| `scheduled-sweep` | clone; push a new branch; create a PR | issue write, org or admin anything |

**What is not established, stated rather than assumed.** The scopes `BOT_TOKEN`
actually holds cannot be read from this checkout — Gitea 1.24.7 exposes them only
to an authenticated session on `/user/settings/applications`, and its
`swagger.v1.json` does not enumerate the vocabulary. So the table above is what
the workflow *requires*, derived from its own source; whether the token is
scoped that narrowly, or is a broad `all` token, is unknown here.

**Decision for the owner:** open `/user/settings/applications`, compare the
granted scopes against the table above, and either narrow the existing token or
issue a second, narrower one for `pr-heal`. If Gitea cannot express *repository
write without pull-request creation*, a single token is an acceptable outcome —
recorded as a decision rather than left as an implicit one.

Job-level `permissions:` were still added, because they do narrow the ambient
token. They are described as exactly that and nowhere as restricting
`BOT_TOKEN`.

### OD-3 — the runner's Docker socket (opened 2026-08-26, owner decision)

`docker-compose.yml:322` mounts `/var/run/docker.sock` into `gitea-runner`. That
mount is root-equivalent on the runner host: anything that can reach the socket
can start a privileged container and read the host filesystem — including the
runner's own `BOT_TOKEN` and the repository secrets.

**Measured 2026-08-26: the service is declared and has never been started here.**
It sits behind `profiles: [runner]`, so `docker compose up` does not bring it up
and no local image exists. The exposure is **latent** — assembled and waiting for
`docker compose --profile runner up` — not active.

*What was done:* the chain that made the socket reachable by unattended remote
code was removed. `auto-healer.yaml` no longer pipes an unpinned installer into a
shell, every action is pinned to a commit, and every image and build input is
pinned. Remote code no longer changes underneath the runner between Sundays.

*What was not done:* the socket stays. `act_runner` requires it to launch job
containers. The alternatives are all **runner-host** changes rather than
repository ones — rootless Podman exposing a user socket
(`DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock`) is the credible
path, and Kaniko is not: Google archived it in June 2025.

*Revisit when:* the runner is actually brought up, or the runner ever executes a
workflow from an untrusted branch. Today `pr-heal` treats the PR checkout as
data and never executes anything from it; that property is what makes the
current arrangement defensible, and losing it changes this decision.

### OD-4 — production-readiness release gate (opened 2026-08-26, owner decision)

The current repository contains reviewed but uncommitted work, while the running
images and corpus predate it. A release is therefore a controlled transition,
not a routine `docker compose up`.

The approved v0.2.1 transition uses a named isolated staging project, external
release evidence, a checksum-recorded logical PostgreSQL archive, and a test
restore. The decisions below distinguish the current operational transition
from later product-scope work:

| Decision | Current state |
| --- | --- |
| candidate Git revision and release tag | **approved:** tag `v0.2.1`; SHA is recorded only after the clean candidate commit |
| staging project or approved maintenance window | **approved:** isolated `snp-v021-staging`, never the default `snp-memory` project |
| PostgreSQL backup/restore-point owner and identifier | **approved:** release operator creates a custom archive, checksum record, and staging restore result before transition; the generated `BACKUP_ID` is retained outside Git |
| container-side capability-change acknowledgement | pending — it must be copied from the release dry run |
| supported-language contract | not a blocker for the v0.2.1 operational release — OD-1 closed as moot; the wiki-only English model it concerned no longer exists |
| content/judge budget | deferred product decision — see T4.1 |
| derived-asset location and retention | deferred product decision — see T4.3 |
| source-health thresholds and quarantine policy | deferred product decision — see Tier 5 |
| `BOT_TOKEN` scopes and runner-host posture | deferred runner decision — see OD-2 and OD-3; runner remains disabled |

**Status: EXECUTION IN PROGRESS.** The owner approved the clean commit/tag,
isolated staging, backup/restore drill, candidate build, controlled restart,
and container-side ingest only after preflight passes. It does **not** authorise
runner activation or the deferred product decisions.

## Prohibited claims in active guidance

Active instructions must not describe:

- a local model daemon or the retired monolithic RAG engine as current;
- anonymous Scout access outside loopback development mode;
- runtime use of a PostgreSQL superuser;
- a developer checkout mounted read-write at `/repo`;
- DOCX or an unimplemented central ingest REST endpoint as supported;
- caller scope as `roles`, `team`, or a magic `all` authority;
- verifier exit `2` as semantic drift or as permission to mutate;
- direct healer use as the closed-loop CI gate;
- a similarity/relevance score threshold for address verification — no such
  threshold exists in code, and `RagChunk.score` is an RRF weight capped near
  `0.033`, so none can be stated as a similarity;
- a mismatched `hint` returning empty or "dead-ending" — `rag_fetch` is no longer agent-facing, but the internal call still
  pre-filters by `path`, so the addressed file is returned regardless;
- the Phase 0 Gate 4 model as deployed for wiki search;
- the local `snpmemory mcp` server as network-reachable, authenticated, or safe to
  expose over HTTP — it is stdio-only and unauthenticated by design;
- any MCP tool other than `wiki_search`/`wiki_read` as a door into the wiki or
  RAG index;
- figure or table extraction as working in the deployed ingester — the
  `snp-scout` image installs `pypdf` only (`scout/requirements.txt`), so
  `pdfplumber` (tables) and Pillow (`pypdf[image]`, figures) are both absent,
  and no figure in an ingested PDF is described no matter how `snp-vlm` is
  configured;
- `metadata.figures_status == "ok"` as evidence that a document's figures were
  examined. **The swallow is fixed** (T5.1): `extract_figures` now re-raises the
  `ImportError` as a `PdfStructureError`, so a Pillow-less installation reports
  `figures_status: "unavailable"` and **no figure count at all**. What remains
  prohibited is the inverse reading — `"no_evidence"` means the parser ran and
  this document captions nothing; it is not a statement that figures were
  described. Nothing in the deployed image describes a figure: `pdfplumber` and
  Pillow are both absent (confirmed in the running `scout` and `sync-job`
  containers), and that is a **decision** recorded in T5.1, not an oversight.

When architecture changes, update implementation and active documents together,
then re-run the stale-claim search described in the review plan. Preserve old
records by changing their banner, not by silently rewriting history.
