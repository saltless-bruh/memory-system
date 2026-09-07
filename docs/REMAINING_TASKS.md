# Remaining Tasks

**Status date:** 2026-08-26
**Branch:** `fix/architecture-security-hardening` (HEAD `2d2b9dd` + uncommitted Tier 1–6 work, 15 commits ahead of `origin/main`, unpushed by request)
**Repo health:** `ruff check .` clean · `ruff format --check .` clean (333 files) · `mypy scout scripts` clean (71 files) · `pytest` **1342 passed, 21 skipped, exit 0**

This document is the working backlog. Every item states what is missing, the
evidence it is missing, what "done" means, and how to verify it. Items are
grouped into tiers by what blocks what — Tier 0 blocks any live demo, Tier 1
blocks the CLI story, and so on.

**Tiers 0–3 are resolved, and Tiers 4–6 are resolved except one deliberate
pause.** Items are kept as "was / is now" rather than deleted, because several
were misdiagnosed the first time and the correction is the useful part.

**What is actually left:**

1. **T4.1** — compiling the remaining articles. Built, planned, and paused at a
   **go/no-go**: it is the only step that spends real model budget.
2. **T4.3** — the `derived/` asset store. A data-model decision nobody has taken;
   the last thing blocking `extract`, the one unimplemented command.
3. **Tier 5's proposed design** — SH-4 and the T5.1 reporting fix are built; the
   quarantine, aggregation and operator affordances are not, and four of the
   audit's decisions are still open.

### v0.2.1 operational release — execution in progress

The owner approved a clean `v0.2.1` candidate tag, isolated
`snp-v021-staging`, a PostgreSQL backup plus test restore, and candidate build /
controlled transition after the release preflight passes. The live default
Compose project remains untouched while staging is prepared. The supported
language contract, additional corpus budget, derived assets, source-health
policy, and runner posture remain separate deferred product decisions; they are
not silently assumed into this operational release.

What is **already done** and not repeated here: grounded body generation
(P1/P6), Step 3 batch compilation (P2/P3/P4-batch/P5/P7), and the local stdio
MCP server. See `artifacts/superpowers/finish-grounded-bodies-2026-08-21.md`,
`finish-step3-2026-08-21.md`, and `finish-mcp-2026-08-21.md`.

---

## Tier 0 — RESOLVED 2026-08-24

All Tier 0 items are fixed and verified live. Kept here as "was / is now" rather
than deleted, because two of them were misdiagnosed the first time and the
correction is the useful part. Plan: `artifacts/superpowers/plan-tier0-2026-08-24.md`.
Evidence: `artifacts/superpowers/execution.md` and `tier0-baseline-2026-08-24.txt`.

### T0.1 — Containers had no external DNS — FIXED

**Was:** `litellm`'s `/etc/resolv.conf` said `NO EXTERNAL NAMESERVERS DEFINED`.
Docker's embedded resolver had no upstream to forward to, because the host's
`/etc/resolv.conf` named only a loopback resolver when the containers were
created. Every model route returned 500.

**Is now:** `ExtServers: [host(192.168.2.253) host(192.168.2.235) …]`; both DNS
probes resolve; `POST /v1/embeddings` returns `200` with a 1024-dim vector. The
fix was `docker compose up -d --force-recreate litellm scout sync-job` — the
containers held a stale copy of a host file that had since been corrected.

**So it does not recur silently:** `docker-compose.dns.yml` is an opt-in
override (`SNP_DNS_SERVERS`, required when the file is used) for hosts that
genuinely cannot supply routable nameservers; `docs/runbook.md` §6.1 documents
the symptom, the three fixes in order, and why no public resolver is defaulted
in; `scripts/preflight_stack.py` reports the condition by name.

### T0.2 — `sync-job` crash loop — FIXED (and it was two bugs, not one)

**Was:** 238 restarts, `FATAL: Initial cold-start sync failed: error:EmbeddingError`.

**The first cause was not DNS.** After DNS was restored the loop continued. The
real trigger was a **stale image**: `snp-scout` was built 2026-08-20T09:57, and
the batch-splitting cap that keeps embedding requests under Gemini's limit
landed in `268af30` at 15:09 the same day. The image sent all 127 chunks in one
request and the provider answered
`BatchEmbedContentsRequest.requests: at most 100 requests can be in one batch`.
`docker compose up` never rebuilds, because only one service carries a `build:`
stanza and the rest reference the `snp-scout` tag.

That is bigger than ingestion: `scout`, the only door into RAG, was running
pre-`268af30` code — before request-scoped auth and document ACLs. **Any "verified
live" result from this stack between 2026-08-20 and 2026-08-24 was verified
against code the repository does not contain.**

**The second cause was the exit-on-failure design.** Docker's restart backoff
resets once a container survives 10 seconds, and this one always did, so the
backoff never accumulated. `scout/sync_job.py::_async_main` now retries
retryable failures in-process with capped exponential backoff (5s → 300s) on
both the cold start and the watch loop, holding readiness cleared throughout.
Permanent failures (an unreadable ACL policy) still exit `1` immediately.

**Is now, verified live:** with `litellm` stopped and `sync-job` restarted —
`t+100s health=unhealthy restarts=0 state=running`, logs showing
`retrying in 5s (attempt 1, readiness cleared)` then `retrying in 10s (attempt 2)`.
Alive, not restarting, and honestly reporting failure.

**So it does not recur silently:** `scout/Dockerfile` stamps
`org.opencontainers.image.revision` from a `SNP_GIT_REVISION` build arg, and
`scripts/preflight_stack.py` compares it against `git rev-parse HEAD`. An image
with no label is reported *unverifiable*, never healthy — a build timestamp
cannot tell a clean build from one made off a dirty tree. Runbook §6.2.

### T0.3 — Health check gating — FIXED, and it was hiding a dated outage

**Was:** the check required **every** model group to be healthy, and `sync-job`
gates on it via `depends_on: service_healthy`.

**The finding that made this urgent:** `background_health_checks: true` with
`health_check_interval: 300` probes every group **288 times a day** with a real
request. `snp-judge` runs on an OpenRouter free key, whose ceiling is **50
requests/day** without purchased credits. The monitor would have spent roughly
six times the judge's entire daily budget before a single page was judged, then
marked the route unhealthy — which, under the old all-or-nothing gate, would
have stopped ingestion. Predicted time to outage: about four hours.

**Is now:** `snp-judge` carries `disable_background_health_check: true`, so
nothing probes it on a timer — and, corrected 2026-08-26, **nothing probes it on
demand either**: this paragraph used to claim an on-demand `GET /health` still
did, which contradicts its own next sentence. `/health?model=` filters the
cached background result, and a route excluded from that loop has no cached
result, so it answers 503 regardless. Read that as *unknown*. To ask for real,
run `scripts/verify_groundedness.py --probe` (runbook §5.1).
The container check gates on `snp-embed` and `snp-llm` only —
`snp-vlm` and `snp-judge` are reported, not load-bearing. It probes
`/health?model=<group>`, which filters the **cached** background result rather
than issuing a live call, so the probe costs no tokens at any interval.
`start_interval` was added to both services (Docker 29.7.2 / Compose 5.5.0).

Note for anyone reading LiteLLM's docs: they say `/health?model=` returns 200
with counts. **This build returns 503** when the targeted group has no healthy
deployment (`_health_endpoints.py`, "surface that as a 503 so monitoring systems
can rely on the HTTP status"). The probe handles both.

### T0.4 — Figures and tables — the real limitation is larger than "no vision route"

**Was recorded as:** "no vision route is configured; 7 figures silently undescribed."
That was wrong. The warning only appears when running on the *host* without
`LITELLM_BASE_URL` in the shell.

**What is actually true:** `scout/requirements.txt` installs `pypdf` only. The
image has neither `pdfplumber` (tables) nor Pillow (`pypdf[image]`, figures), so
the deployed ingester cannot extract a figure no matter how `snp-vlm` is
configured. Tables report this honestly (`tables_status: unavailable`).

**Figures do not.** Inside the image `pypdf` raises
`ImportError: pillow is required to do image extraction`; the per-page
`except Exception: continue` in `extract_figures` swallows it; `figure_count`
comes back `0`; and `figures_status` is recorded as **`"ok"`** — for a document
with 7 figures. Verified by running the same page through pypdf on the host
(2 images) and in the image (ImportError). Both facts are now prohibited claims
in `docs/ARCHITECTURE_STATUS.md`.

**Still open** (moved to Tier 5, where it belongs — it is SH-1/SH-2 in concrete
form): decide whether the deployed ingester should gain `pdfplumber` and
`pypdf[image]`, and stop reporting `"ok"` for a capability that is absent. A
count of zero from a parser that could not look is not a count of zero.

## Tier 1 — RESOLVED except one blocked command

`docs/CLI_SPEC.md` specifies 28 commands. **Twenty-seven are implemented**, all
declared, tested, and carrying an `Exposure` decision:

```
check  compile  compile-cancel  compile-plan  compile-status  down
fetch  gate  heal  ingest  init  install-agent
logs  mcp  mcp-config  mint  plan-articles  propose
read  schema  search  status  up  verify-addresses
verify-groundedness  verify-secrets  verify-vault
```

The security boundary was proved live: one hint against a document whose ACL is
`{ai_eng, blueteam}` gives `ai_eng` exit 0 `ok`, `blueteam` exit 0 `ok`,
`infra` exit 1 `no_source`, `redteam` exit 1 `no_source`.

**One remains, blocked on another tier:**

| Command | What it unlocks | Blocked on |
| --- | --- | --- |
| `snpmemory extract --path │ --dir` | figures + tables → `derived/` | **T4.3** — the asset store's location, retention and ACL inheritance are undecided, and T5.1 means the deployed image cannot extract a figure anyway |

`install-agent` landed with Tier 3, which is what unblocked it.

**Every new command must:** register through `scout/cli/declarations.py`, honour
the exit-code contract (0 ok / 1 semantic / 2 infrastructure — 2 never
triggers a mutation), appear in `snpmemory schema`, and get an
`Exposure` decision in `scout/cli/mcp_policy.py` (the `undecided_commands()`
guard fails the test suite otherwise — this is deliberate).

### T1.1 — `snpmemory --help` exited 2 — FIXED

**Was:** `--help` and a bare `snpmemory` both exited **2** — the infrastructure
code — after printing the tool's own "this is a bug in snpmemory" banner. Any CI
smoke test shelling out to `snpmemory --help` read a healthy binary as a broken
one.

**Is now, verified live:** `snpmemory --help` → exit 0, `snpmemory` → exit 0,
banner absent. The cyclopts help/version paths return `None` rather than raising
`SystemExit`, so `scout/cli/app.py` treats `None` as "cyclopts already handled
this and printed" instead of as a contract violation. Regression test in
`tests/test_cli_core.py`.

---

## Tier 2 — RESOLVED 2026-08-25

The local stdio server (`scout/mcp/local_server.py`, 4 tools: `verify`,
`plan_articles`, `compile_plan`, `compile_status`) was built and verified in an
earlier tier. T2.1–T2.4 are now closed. Plan:
`artifacts/superpowers/plan-tier2-2026-08-25.md`; evidence:
`artifacts/superpowers/execution.md` and `finish-tier2-2026-08-25.md`.

### T2.1 — Nothing told an agent the local server existed — FIXED

**Was:** `packages/snp-agent/manifest.json` listed only `basic-memory` and
`scout`; `scripts/export_mcp_config.py` emitted only `snp-wiki` and `scout`. An
agent installed from the package got the two read paths and none of the
authoring path.

**Is now:** all three surfaces emit three servers — and there turned out to be
**four** surfaces, not the two the audit named: `scripts/install-agent.sh` also
scaffolds a `.mcp.json`, and it is the one an installed agent actually reads.
The local entry is `{"command": "snpmemory", "args": ["mcp", "--root", "<checkout>"]}`,
stdio only, no URL and no token.

The wiki server was being called two different things. The manifest said
`basic-memory` (the engine's name) while the exporter, the installer and
CLAUDE.md all said `snp-wiki` — and an agent's tool namespace comes from the
client-config key, so `snp-wiki` is the name that is true. The manifest was
renamed to match. See **T3.0** for the package files that still carry the old
namespace in their instructions.

**So it does not recur silently:** tests assert the manifest's server set equals
the exporter's (for both manifests), that `required_tools` equals what
`build_server().list_tools()` actually serves, and that the installer's scaffold
carries all three. `docs/CONNECT_AGENTS.md` now states which server owns which
job in three rows.

### T2.2 — A task handle resolved against the current working directory — FIXED

**Was:** `status_for(plan_path)` derived the staging directory from a
cwd-relative plan path, so an agent that called `compile_plan` from one
directory and `compile_status` from another got `NOT_STARTED` for a run happily
in progress — the one answer that invites starting the batch a second time.

**Is now:** handles are **absolute**, so a stored handle names the same batch
wherever it is used, and `resolve_plan_path` refuses a plan path that resolves
outside the checkout — which also bounds where a tool call can write, since the
staging directory follows the plan path. Verified live: the same batch reported
from the repository root and from `docs/` via a cwd-relative path returns one
identical handle and one identical state.

Note the rule that was **not** adopted: anchoring relative paths to the root. It
was implemented, live-tested, and reverted, because it was wrong in both
directions — `cd docs && compile-status ../artifacts/plan.json` was refused with
"pass a path under `<root>`", untrue of a path that plainly is under it, while
`cd docs && compile-status plan.json` would silently have read `<root>/plan.json`
instead of the file the caller was looking at. A relative path resolves the way a
shell resolves it; `root` is a **boundary**, not an anchor.

`snpmemory mcp --root <dir>` pins the working directory at startup, so a client
may launch the server from its own directory. That is a `chdir`, not a stored
value, because `invoke()` resolves configuration and `.env` from `Path.cwd()` on
every tool call.

### T2.3 — Staging was not guarded against a plan edit — FIXED

**Was:** the plan file is hand-editable by design and nothing pruned staging when
it changed, so `--resume` after an edit reused pages generated from the **old**
plan and reported success.

**Is now:** the plan's content fingerprint is written into `.run.json` at start,
and a resume whose fingerprint differs **refuses**, naming the drift by slug
(`1 changed (beta); 1 added (gamma); 1 removed (delta)`), with `--no-resume` as
the escape hatch. The hash covers the *parsed* article list and only the fields
that reach generation, so reformatting a plan, sorting its keys, or renumbering
every `section` leaves a running batch alone. A staging directory written before
fingerprints existed **warns** rather than refusing — a batch already in flight
must not be stranded by the upgrade.

### T2.4 — A backgrounded batch was unsupervised — FIXED

**Was:** `compile-plan --background` returned a handle and detached. A crashed
run read as `STALLED` forever, there was no way to cancel one, and staleness was
inferred from a pid — which says a process exists, not that it is working, and
which can be reused.

**Is now:**

* `TaskState` has terminal `FAILED` and `CANCELLED`. A run records why it
  stopped, with its exit code, before exiting — including only the exception
  *class* for an unexpected crash, because a traceback here can carry anything.
* A **heartbeat** is written as each article is staged, so `STALLED` is
  time-based: a live pid whose heartbeat is older than the TTL reports
  `process N is alive but has finished no article in Xs — it may be hung`.
* The status payload carries `poll_interval` (5s) and `ttl` (900s) so a caller
  is told how often to poll and when to stop trusting a `running` claim, plus a
  `terminal` flag so it knows when to stop polling at all.
* `snpmemory compile-cancel <handle>` writes a request the batch reads
  **between articles**, where staging is consistent. Verified live against a real
  detached run: article 2 finished and stayed staged, article 3 never started,
  state `cancelled: 2/3`, and re-running resumed from the staged pages.
* `compile-status` exits **1** for `failed` and `cancelled`. It exited 0, which
  would have told a caller chaining `compile-status && publish` to proceed on a
  dead batch.

### T2.5 — MCP Tasks are not adoptable at the pinned versions — OPEN, and priced

The repository hand-rolled a long-running-job protocol before MCP had one, and
both pinned SDKs now implement Tasks (`mcp` 1.29.0 exposes `CreateTaskResult` /
`GetTaskRequest` / `CancelTaskRequest`; `fastmcp` 3.3.1 exposes `TaskConfig`).
Adopting it was planned as one decorator argument. **It is not**, and the
measurement is recorded so nobody re-derives it:

* every fastmcp task path is gated on **pydocket** (`fastmcp[tasks]`), a
  distributed task system requiring **`redis>=5`** and 13 other packages;
* without it `get_task_capabilities()` returns `None`, so the server advertises
  no task capability and the handshake cannot negotiate;
* `TaskConfig.validate_function` calls `require_docket` at **registration**, so
  declaring one without the extra does not degrade — `build_server()` raises and
  there is no server at all;
* task-augmented functions must be `async`; these tools are synchronous wrappers
  around a blocking command path.

There is also a **version fork** worth knowing before anyone builds on it: Tasks
shipped as an experimental core feature in **2025-11-25** and moved to an
extension (`io.modelcontextprotocol/tasks`) in **2026-07-28**. The pinned SDK
reports `LATEST_PROTOCOL_VERSION = 2025-11-25` and its `Task` model carries
`ttl` / `pollInterval` — the core-experimental names, not the extension's
`ttlMs` / `pollIntervalMs`.

**The decision, and what would change it.** A `taskId` is scoped to the server
process; the plan-path handle is a path on disk backed by a staging directory
and a `.run.json` carrying state, heartbeat, TTL and poll interval, and it
survives a server restart, a reboot, and a client that has never heard of Tasks.
Requiring Redis to obtain a weaker record is the wrong trade **today**. Revisit
if the stack gains Redis for another reason, or if fastmcp ships a task backend
with no external dependency. `tests/test_local_mcp_server.py` asserts the
condition in both directions, so the day it changes the decision is revisited
deliberately rather than by accident.

## Tier 3 — RESOLVED 2026-08-25

The package is now an **Agent Plugins 1.0.0** plugin, the 25-file gap is closed
by a recorded decision rather than an accident, and no installed agent is told to
call a tool that does not exist. Plan:
`artifacts/superpowers/plan-tier3-2026-08-25.md`; evidence:
`artifacts/superpowers/execution.md` and `finish-tier3-2026-08-25.md`.

### T3.0 — Package instructions named the wrong MCP tool namespace — FIXED

**Was:** five distributed files told agents to call `basic-memory.search_notes(…)`
/ `read_note(…)`. An agent's tool namespace comes from the key its client config
gives a server, and every surface that configures a client says `snp-wiki`, so
that tool has never existed for anyone.

**Is now:** renamed across 30 files in all three trees — including
`snp-read-wiki-page`'s **frontmatter description**, the text an agent reads to
decide whether the skill applies at all. `basic-memory` survives only where it
names the *engine* (`Roadmap`) or the *container* (`snp-bootstrap-system`'s list
of compose services), because there it is correct.
`tests/test_agent_tool_namespace.py` enforces it per file and carries an
allowlist keyed by filename with the reason, so a **new** mention has to be
classified rather than absorbed.

### T3.3 — No test proves the agent's first hop — ACCEPTED RISK, tracked

**The gap.** `tests/integration/test_live_end_to_end.py` builds a temporary
corpus and reads pages off disk. It requires `LITELLM_BASE_URL` and `POSTGRES_*`
and never contacts **basic-memory**. So nothing in this repository proves that
`search_notes` would surface the page an agent needs — which is *step 1* of the
query workflow in `AGENTS.md`. Everything downstream of it is well covered.

The test used to be named `test_live_wiki_sources_drive_rag_retrieval_end_to_end`,
which claimed the hop it does not make. Renamed to
`test_page_sources_drive_live_rag_retrieval_end_to_end` — but **renaming does not
close this**, and recording it in a docstring would be too easy to forget.

**Acceptance criterion for closing it.** A separately runnable integration test
that, against a live `snp-wiki`:

1. publishes a known page into a real vault snapshot;
2. calls `search_notes` with a query whose answer is that page, and asserts it
   is returned;
3. calls `read_note` on the returned identifier and asserts the body matches.

**Why it is accepted rather than scheduled.** It needs the basic-memory MCP
reachable from the test process and a way to publish into the replica the
container reads — neither of which the current integration harness does. It also
interacts with **OD-1**: the model that would rank step 2 is the one under open
decision, and a test written against `bge-small-en-v1.5` would need rewriting if
that changes.

**Owner:** repository owner. **Target:** with OD-1, or with the next live
integration work, whichever comes first.

### T3.1 — Six `SKILL.md` frontmatters were not valid strict YAML — FIXED, and it was three problems

**Was recorded as:** six files in the package with an unquoted `description:`
containing `: `.

**What was actually true:**

* they are not in the package at all — they are in `.agent/skills` **and**
  `.claude/skills`, which are byte-identical copies, so it was 12 files;
* the fix already used for the other eight skills — a `>-` folded scalar — puts
  a `>` in the frontmatter, and the Agent Skills specification names `<`/`>`
  there as a **prompt-injection risk**. So the existing "fix" was itself a
  finding: 26 files carried angle brackets.

**Is now:** one pass rewrote all 42 descriptions as double-quoted single-line
scalars, and `->` arrows became "then".
`tests/test_agent_skills_spec.py` (211 cases) encodes the specification's rules —
strict YAML, `name` matching its directory, description length, no angle
brackets, known keys only — across all three skill roots. The rules are encoded
rather than importing `skills-ref`, whose own authors describe it as "intended
for demonstration purposes only and not meant to be used in production".

### T3.2 — `install-agent` did not exist — FIXED

`snpmemory install-agent [dir] [--dry-run] [--confirm]` wraps
`scripts/install-agent.sh`. `3` for a target that is not a directory, `5` when
the target already has an `.agent/` (somebody else's configured project), `2` if
the script cannot run. Verified live end to end: the argv from the *installed
project's own* `.mcp.json` starts the server and lists all four tools.

### T3.3 — The 25-file gap was a decision nobody had made — DECIDED and ENFORCED

**Was:** `diff -rq .agent packages/snp-agent` → 25 differences, all "Only in
.agent". And it was **structurally unguarded**: the parity test covered
`.agent` ↔ `.claude` for four subtrees and `packages` ↔ `.agent` for *root files
only*, so the package could lose any number of components with no test noticing.

**Is now: the superpowers layer is repo-local, deliberately.** It is this
repository's own development discipline. Shipping it would tell a consumer's
agent to write brainstorms and plans into *their* `artifacts/superpowers/`, for
work that has nothing to do with the memory system. `plugin.json` declares both
halves — `ships` (8 skills, 6 workflows, 3 instructions, 1 rule) and `repoLocal`
(the `superpowers-` prefix, 1 rule, 7 instructions) — and two tests hold the
package to it in **both** directions: a declared component going missing fails,
and a repo-local component leaking in fails. The leak check also asserts the
excluded files still exist in `.agent/`, so "repo-local" cannot quietly become
"deleted".

### T3.4 — Agent Plugins 1.0.0 adopted

`packages/snp-agent/manifest.json` was bespoke and only this repository's own
tests understood it. It is replaced by `plugin.json` + `mcp.json`, validated
against schemas vendored under `tests/fixtures/` the way the CLI Spec is. What
the migration corrected, beyond shape:

* MCP servers were declared **inline in the manifest**, which the specification
  explicitly forbids; they are now in `mcp.json` with **typed** transports.
* `load_manifest()` pins the schema identifier. Those are immutable and a new
  release must use a new one, so a mismatch is a migration to make rather than a
  version to tolerate.

**The limitation to know before "fixing" it:** a portable plugin **cannot** pin
the memory-system checkout. `${PLUGIN_ROOT}` names the *installed plugin's*
directory, every resolved path must stay inside it, and the checkout is outside
by construction. So `mcp.json` ships `env: {"SNP_MEMORY_ROOT": ""}` and
`snpmemory mcp` reads it as a `--root` fallback, exiting `3` and naming the
variable when it is empty rather than serving whichever directory the client
started in. `snpmemory mcp-config` writes the real path, because it runs *from*
the checkout and therefore knows it.

### T3.5 — There were **five** config surfaces, not three — FIXED

Tier 2's risk R6 named three (exporter, manifest, docs) and its step 4 found a
fourth (`install-agent.sh`). The fifth was `scripts/export_agent_bundle.py`,
which carried its own hardcoded per-client configs naming `basic-memory` and
authenticating with **`SCOUT_AUTH_TOKEN`** — while the documentation, the
exporter and the installer all use `SCOUT_AUTH_HEADER` (the *complete* header
value). A user who followed `docs/CONNECT_AGENTS.md` got a config from that path
that could not authenticate, and no `snpmemory` server at all.

All five now come from `export_mcp_config.generate_config()`. It also **merged**
rather than replacing the target file, which it had not been doing — overwriting
somebody's `.mcp.json` is data loss.

## Tier 4 — Content and the knowledge graph

### T4.1 — 12 of 15 planned articles are uncompiled — READY, awaiting a go/no-go

**The count was wrong, and the reason matters.** `plan-articles` proposes 15.
Of the 5 compiled pages only **3** correspond to headings in the current
proposal; `advantages-and-disadvantages-of-deep-learning` and
`convolutional-neural-networks` do not, because the plan that produced them was
hand-edited. So **12 remain**, not 10.

Regenerated and ready:

* `artifacts/plans/computers-12-00091.plan.json` — all 15, byte-stable, no model
  call.
* `artifacts/plans/computers-12-00091.recommended.json` — **6 articles**, the
  recommended edit.

**Why 6 and not 12.** A heading is not a page. Measured prose owned by each
proposed heading: `biometrics` **62 characters**, `the-future-directions` 584,
`dl-properties-and-dependencies` 688, `machine-learning-and-deep-learning` 939
(a parent heading whose children are 2.1 and 2.2), `introduction` 1001.
`conclusions` is large but summarises the paper rather than being a concept. The
six recommended each own several thousand characters and are conceptual:
`different-machine-learning-categories`, `some-deep-learning-applications`,
`clinical-imaging`, `deep-learning-approaches`, `mobile`,
`recommender-systems-rs`.

**Budget, which is why this is a decision and not a task.** Generation runs on
**Gemini** (`gemini-3.5-flash`) and does not touch the free tier; only the
**judge** does (`openrouter/…:free`, **50 requests/day**). Estimated judge calls
including the `verify-groundedness` pass afterwards: **6 articles → ~17–23**;
**12 articles → ~29–41**. Six is comfortable; twelve risks exhausting the day's
budget mid-run. A run that exhausts is resumable (Tier 2's staging), not lost.

**Do this on the cleaned corpus, not before it.** The bibliography lift landed
first deliberately: compiling against the polluted index would have minted 10
addresses against 18% noise.

### T4.4 — One existing page has a bad hint

`the-key-distinctions-between-deep-learning-and-machine-learning`'s minted hint
is **the paper's own title**, so it retrieves the paper's `Citation:` block
rather than the section it describes. `verify-addresses` reports PASS —
correctly, the address does retrieve its own file at top rank — so this is a
retrieval *quality* problem the address contract does not cover. It is one of
the two pages from the hand-edited plan. Re-mint it alongside T4.1's compile.
See `artifacts/superpowers/retrieval-baseline-2026-08-25.md`.

### T4.2 — The 87-reference graph — BUILT (extraction and edges), pending content

**The question it posed** — "does each reference become a page, or a graph edge
on the citing page?" — is **answered: neither, exactly.** Current GraphRAG
practice models `Paper`, `CitationPaper` and `Chunk` as distinct node types. An
outward reference is **metadata on the citing page**, not a wiki page; it
becomes a `[[wikilink]]` only if that work is itself ingested into `raw/`. That
satisfies R-1.5 without minting 87 stub pages that answer nothing.

**Built:**

* `scout/references.py` parses the reference list — **87 of 87** entries,
  deterministic, no model call. `index`/`text`/`year`/`url`/`doi` are reliable;
  `title` is `None` rather than guessed when the entry's shape is ambiguous
  (**18 of 87**), and every entry keeps its verbatim text so no citation is lost.
* The source carries **124 inline `[N]` markers** resolving to **78 distinct
  references**, so the edges are derivable from the text.
* A compiled page gains an **optional `## Works Cited`** section listing the
  works its own retrieved passages cite. Optional deliberately: requiring it
  would fail every page compiled before it existed. Resolved from the
  *passages*, not the generated prose — a citation the page never saw would be a
  fabricated edge.

**Pending:** the edges only appear on pages compiled from now on. The 5 existing
pages predate the section. They gain it if and when they are recompiled.

### T4.3 — The `derived/` asset store — STILL DEFERRED, and now the critical path

Figures and tables extracted from sources need somewhere to live that is
neither `raw/` (read-only, R-3.1) nor `wiki/` (compiled prose). `derived/` is
referenced in `docs/CLI_SPEC.md` and in the code that would write to it, but
the store itself, its retention policy, and its ACL inheritance from the source
document are undecided. **Deferred pending discussion** — flagged here so it is
not lost. Blocks `snpmemory extract`, now the **only** unimplemented command of
the 28 specified.

Of the three open questions, one is not a preference: **an extracted figure must
inherit its source document's ACL.** It has to be stated rather than assumed,
because an asset store that does not inherit is a hole in the ACL model —
a figure cut from an `{ai_eng}` document and stored unlabelled is readable by
everyone. The other two (location, retention) are genuine choices.

---

## Tier 5 — Source health (SH-1 … SH-10)

`docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md` is written and its findings stand;
**none of the remediation is built.** Summary of what each finding demands:

| ID | Finding | What it needs |
| --- | --- | --- |
| SH-1 | Integrity checks cannot detect the failures that actually occur | Checksums prove a file is unchanged, not that it yields evidence. Health must be measured on the **indexed chunks**, not the bytes |
| SH-2 | The vault linter accepts a source that yields no evidence | `verify-vault` must fail (or warn distinctly) when a cited source has zero retrievable chunks |
| SH-3 | Provenance can diverge silently from the evidence served | The `sources[]` address and what `rag_fetch` actually returns must be checked against each other, not assumed consistent |
| SH-4 | A quarantined source is indistinguishable from "no match" | Needs a `NO_EVIDENCE` status distinct from an empty result — see below |
| SH-5 | A daily full rescan re-runs paid model calls on unchanged files | Content-addressed skip; interacts directly with T0.2's restart loop |
| SH-6 | A separate validator will drift from the ingest parser | Health checks must call the **same** parser ingestion uses, not a parallel implementation |
| SH-7 | Parsing untrusted uploads at scale is a new attack surface | Sandbox/resource-limit the parser before accepting third-party uploads |
| SH-8 | Word and Excel are not supported, and that is deliberate | Document the decision so it reads as scope, not as a bug |
| SH-9 | A false-positive quarantine deletes real evidence | Quarantine must be reversible and never destructive |
| SH-10 | "Automatically fix corrupted data" is mostly not possible | Set expectations: detect and report, do not promise repair |

**The cross-cutting gap (from the audit's FUTURE WORK):** there is no handling
path for unusable source data at all. Nothing aggregates it, nothing surfaces
it, there is no quarantine, the status taxonomy has no `NO_EVIDENCE` member,
the wiki linter is blind to it, and an operator has no affordance to act on it.
The minimum viable slice is: add `NO_EVIDENCE` to the status taxonomy, teach
the linter to check indexed chunks rather than file presence, and add a
`--report` mode that lists affected sources without mutating anything.

**Two of these are now built** (2026-08-25):

* **SH-4** — `VerifyStatus.NO_EVIDENCE` exists and `snpmemory verify-addresses`
  reports it as its own column. A source that yields nothing at all is no longer
  labelled the same as an address whose phrase went stale, and `mint` stops
  trying candidates when it sees one, because no hint can fix a source with no
  chunks.
* **SH-1/SH-2 in the parser** — see T5.1 below.

**The audit is partly stale, and it now says so.** Its §6 decision 5 is
**resolved**: the two files it uses as worked examples are absent from `raw/`
and no wiki page cites either. The findings stand as classes of failure; the
worked examples describe a corpus that is gone. A banner at the top of the audit
records this, and `tests/test_docs_contract.py` fails if the document ever names
an absent source without one. **Four decisions remain, not five.**

`docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md` §5 has a recommended build order and
§6 lists the decisions that need the owner before the rest of it starts.

### T5.1 — The ingester reported `ok` for a capability it does not have — FIXED (reporting), DECIDED (capability)

**Was:** with Pillow absent, `page.images` raises `ImportError`; the per-page
`except Exception: continue` in `extract_figures` swallowed it on **every**
page, so a document with 7 figures was recorded as `figures_status: "ok"`,
`figure_count: 0` — examined successfully, nothing found.

**Is now:** the `ImportError` is re-raised as a `PdfStructureError`. A missing
capability is a fact about the *installation*, not about the page. The status
taxonomy has three values where it had two:

| Value | Means |
| --- | --- |
| `ok` | the parser ran and found something |
| `no_evidence` | it ran fully and found nothing — a fact about the **document** |
| `unavailable` | it could not look — a fact about the **installation** |

A parser that could not look now reports **no count at all**, rather than a
count of zero. The per-page swallow that a corrupt image stream needs is kept,
and has its own test.

**Decision 1 taken (2026-08-25): the deployed ingester does not extract figures
or tables, and that is deliberate.** Confirmed in the running containers —
`scout` and `sync-job` both have `Pillow: ABSENT` and `pdfplumber: ABSENT`.
Adding them grows the image and widens the parser attack surface (SH-7) for one
document. Revisit when a second document arrives, or alongside T4.3.

Worth knowing when it is revisited: the 2026 answer is no longer
"`pdfplumber` + `pypdf[image]`". **Docling** (IBM Research — RT-DETR layout model
on DocLayNet plus TableFormer, CPU-viable) is the production-RAG choice, and its
layout awareness would also identify the reference section structurally rather
than by regex, which is what T4.2 parses by hand today.

**Remaining:** confirming the fixed behaviour *inside* the container needs the
image rebuilt — the running one is built from `2d2b9dd`. The precondition is
verified there; the fix is verified on the host against the exact `ImportError`
pypdf raises.

---

## Tier 6 — Housekeeping and pending decisions

### T6.1 — 15 commits are held locally, unpushed

Held at the owner's explicit instruction ("Hold"). The five untracked wiki
pages are to be kept but **not** pushed. `uv.lock` and `wiki/index.md` are also
modified in the working tree. Nothing to do until the hold is lifted — recorded
so the state is not mistaken for an oversight.

### T6.2 — `/doctor` findings — the repository one is FIXED

**Was:** a stray `docs/news-file_need-check/CLAUDE.md`, loaded as project
instructions for that whole subtree.

**Is now:** renamed to `AGENT_POLICY_SNAPSHOT.md`, so it is documentation. No
`CLAUDE.md` exists under `docs/`.

**Two things found while fixing it, both needing the owner's decision — I did
not act on either:**

1. That file is **byte-identical to `~/.claude/CLAUDE.md`**, the owner's
   *personal global* working policy, and it is **tracked**. A committed copy of a
   personal global file can only drift from the real one, and it would travel
   with the repository if the branch is ever pushed. Recommend deleting it; it is
   authoritative where it already lives.
2. `docs/news-file_need-check/computers-12-00091.pdf` is an **untracked 2.5 MB
   byte-identical duplicate** of `raw/papers/computers-12-00091.pdf` (same md5).
   Left alone in case it was placed deliberately.

The remaining `/doctor` findings are the owner's machine, not this repository,
and are recorded rather than acted on: duplicate PATH entry at `~/.bashrc:54`,
Claude Code 2.1.237 → 2.1.238, auto mode not the default.

### T6.3 — 21 live-integration tests errored rather than skipped — FIXED

**Was:** `uv run pytest` reported `772 passed, 21 errors` on a machine with no
stack. A default test command that never reports green trains everyone to read
past the summary line — the condition under which a real regression gets waved
through.

**Is now:** `SNP_INTEGRATION_PROJECT` **unset** means nobody asked for these, so
they **skip**, and the skip message names the variable that would run them.
**Set but incomplete** still **fails**, loudly, with the missing names — a skip
there would hide the thing somebody was trying to test.

`uv run pytest -q` → **1342 passed, 21 skipped, exit 0.**

### T6.5 — A failing cold start re-parsed the corpus on every inner retry — FIXED

**Was:** `sync_once` retries three times and each attempt re-parsed every
document before reaching the embed call that failed. Cost scaled with the corpus
rather than with the failure.

**Is now:** `scout.ingest.ParseCache`, keyed by **identity** — `(mtime_ns,
size)`, not path — so a changed file is parsed again and a stale reuse is
impossible. Cleared once an index succeeds, because a cache that outlived its
cycle would hold a corpus-worth of parsed text to save work nobody was going to
repeat. Three documents across three attempts now cost **three parses, not
nine**.

### T6.4 — `ruff format` — DONE, but landed at the wrong moment

**Is now:** 94 files reformatted; `ruff format --check .` reports all 333 clean.
Correctness verified — identical test count before and after (1342), clean lint
and type check.

**The sequencing was wrong, and it is recorded rather than smoothed over.** This
task's own point was that the diff "would bury real changes in review", so it
should land "when no other work is in flight". It was run with roughly **140
files of Tier 4–6 work uncommitted**, so the formatting is mixed into that diff
— exactly the outcome the task existed to prevent. The tree's content is correct
either way (`ruff format` is deterministic and idempotent); what was lost is
reviewability. Recovering it means committing the functional work first and the
formatting second, which is a commit decision and therefore the owner's.

---

### T6.6 — Tag a release, so the agent installer can be pinned — OPEN

`scripts/install-agent.sh` clones the repository at a **moving ref**, and the
install path documented at the top of that file and in `docs/CONNECT_AGENTS.md`
pipes it into `bash`:

```
scripts/install-agent.sh:6    curl -fsSL .../main/scripts/install-agent.sh | bash
scripts/install-agent.sh:36   git clone --depth 1 <repo>      # no ref -> default branch
```

Everything else a workflow or a build executes is now pinned (runbook §2.1).
This is the one remaining unpinned remote-execution path, and it is the one
aimed at other people's machines.

**Why it is not simply pinned.** A commit SHA would freeze every future curl
install on one old revision — worse than the moving branch. The fix is a release
tag, and the repository has **0 tags**.

*Done so far:* the installer takes `SNP_AGENT_REF` (default `main`), fails
loudly on an unknown ref, and prints the commit it installed from, so an install
is identifiable after the fact.

*To close:* tag a release, default `SNP_AGENT_REF` to it, and update the
documented curl command to fetch the installer at that tag rather than `main`.

**Owner decision**, coupled to the deployment-coherence commit — there is no
honest revision to tag while 15 commits and a large working tree are held.

## Codex's residual-risk review (2026-08-26) — where each item lives

Nine items, none of them "todo". Each is either closed, an owner decision, or a
tracked risk with an owner and an acceptance criterion. Findable by number.

| # | Item | State | Where |
| --- | --- | --- | --- |
| 1 | The deployed system is not reproducible from current source | **owner decision** — the precondition for 2, 3 and for validating the pinning work | T6.1 (commits held) + *Deployment coherence* below |
| 2 | Wiki retrieval is English-biased | **owner decision** | OD-1 |
| 3 | No end-to-end test against the real basic-memory MCP | **tracked risk** | T3.3 |
| 4 | Capability fingerprints depend on human discipline | **CLOSED 2026-08-26** | `tests/test_parser_golden.py` — a parse change that forgets `PARSER_REVISION` fails; unknown environments fail under `SNP_CANONICAL_ENV=1` |
| 5 | Bad-source lifecycle incomplete | **tracked risk** | Tier 5 (SH-1 … SH-10) |
| 6 | Judge health semantics misleading | **CLOSED 2026-08-26** | runbook §5.1 + `config/litellm/config.yaml`; the endpoint answers *unknown*, and `--probe` answers for real |
| 7 | Derived assets are a design hole | **owner decision** | T4.3 |
| 8 | The corpus is too small to show usefulness | **owner decision** | T4.1 (compile go/no-go) |
| 9 | Supply chain and runner trust | **CLOSED (repository side), decision recorded (host side)** | runbook §2.1 + OD-3; `curl \| sh` removed, 9 actions + 4 images + 3 bases + 246 packages + 2 apt versions pinned; the socket stays, costed |

### Deployment coherence — the precondition, not a peer

Items 1, 3 and the validation of everything pinned in this pass all wait on the
same act: commit the held work, rebuild the images with a clean revision stamp,
and re-ingest through the container.

Until then, and stated plainly so nobody reads the pinning work as more than it
is: **no image in this repository has been built from the pinned inputs.** The
three Dockerfiles, the three hash-locked requirement files and the apt pins are
written and statically verified; the first build that exercises them is the
release build. That build's acceptance criteria are in
`artifacts/superpowers/plan-residual-risk-2026-08-26.md`, step 18.

### Production-readiness release gate — OPEN

The next release needs more than a clean Git revision: it needs an approved
staging or maintenance window, a PostgreSQL restore point, an exact
container-side capability acknowledgement, and a release manifest naming the
image digests and lockfiles that were actually built. These are owner decisions
recorded in OD-4, not defaults an automation may invent. Until the owner lifts
the hold, repository-only preparation may proceed but no build, restart,
re-ingest, push, or runner activation is authorised.

## Suggested sequence

Tiers 0–3 are done, and Tiers 4–6 are done except the items below.

1. **T4.1 — the go/no-go.** Compile the recommended 6 articles (or the full 12).
   Everything else is ready; this is a budget decision, not an engineering one.
   Do it on the cleaned corpus, which is now in place.
2. **T4.4** — re-mint the one page whose hint is the paper's own title. Cheap,
   and it belongs with (1).
3. **T4.3** — decide the `derived/` asset store. Three answers needed: where it
   lives, its retention policy, and — the one that matters — whether an
   extracted figure inherits its source's ACL. It must, but that has to be
   *stated*: an asset store that does not inherit is a hole in the ACL model.
   Unblocks `extract`, the last unimplemented command (27 of 28 exist).
4. **Tier 5's remaining design** — quarantine, aggregation, an operator
   affordance. Four audit decisions are open first; §5 has the build order.
5. **T6.1** — the push hold. Now covering 15 commits plus a very large working
   tree.

**Two operational gaps found during Tier 4–6 and not yet fixed** (both recorded
in place): `snpmemory ingest` cannot run from the host without exporting `.env`
by hand, and the corpus now differs depending on whether ingestion runs on the
host or in the container, because the host has `pdfplumber` and the container
does not.

**Re-verification debt (from T0.2):** `scout` ran pre-`268af30` code from
2026-08-20 to 08-24 — before request-scoped auth and document ACLs. Any "verified
live" claim made against this stack in that window was verified against code the
repository does not contain. The Tier 1 ACL boundary check was re-run afterwards
and holds; anything else from that window should be re-run before it is quoted.
