# Remediation plan — flaws, causes, solutions

> **DOMAIN REFERENCE, NOT SNP DEPLOYMENT AUTHORITY** — a live record of
> flaws and decisions, but a side-source as of 2026-09-15. The authority for
> current findings and their disposition is `docs/AUDIT_2026-09-15.md`.

**Living document.** Opened 2026-09-14. This is the forward-looking counterpart
to `docs/DEFECT_REGISTER_2026-09-09.md`, which is a historical log. The register
records *what was found*; this records *what will be done about it*.

## How this document is maintained

| Status | Means |
|---|---|
| `OPEN` | flaw confirmed, no fix started |
| `DECIDED` | solution agreed, not built |
| `BUILDING` | in progress |
| `VERIFIED` | fix shipped **and** control-tested — observed failing before it was observed passing |
| `CLOSED` | `VERIFIED`, plus one further cycle with no recurrence |

**A fix is never `VERIFIED` on a green result alone.** This project's own history
is the argument: the sparse arm returned nothing for 35 of 40 queries while every
test passed; all four `verify_vault` tests monkeypatch the function holding the
bug; one test *asserts* the duplicate-tool-name configuration is correct. A test
that has never been watched failing is not evidence.

Add rows when new flaws are found. Move rows to `CLOSED` only after a clean
cycle. Do not delete rows — a closed flaw that recurs needs its history visible.

---

## Group A — Blocks giving the system to anyone else

### A1 · The vault is one repository · `OPEN` · register #29

**Flaw.** Department scoping protects *search*, not *distribution*. Anyone handed
a clone gets all 434 pages regardless of clearance.

**Why.** RLS was built at the index layer because that is where retrieval
happens. Git has no equivalent boundary, and one repo was correct for one user.

**Measured.** 431 of 433 indexed documents carry ACL
`{redteam,blueteam,ai_eng,infra}` — every department. Only 2 are narrow, and both
arrive by the `raw/` corpus lane, not the vault.

**Solution.** GitLab self-hosted, one repository per department inside a group.
Protected branches and MR approval come with it. **This gates everything else in
this group** — until it moves, the system cannot leave this machine.

---

### A2 · Vault text leaves the network · `OPEN` · register #67

**Flaw.** Page content egresses to Google on every ingest; every question
egresses on every search. No diagram, slide or runbook said so until 2026-09-14.

**Why.** LiteLLM was drawn as an internal gateway and the routes behind it were
never surfaced. The security boundary on the architecture diagram is captioned
*"only one door in"*, which implied containment that does not exist.

**Measured.** Inside the running container: `LITELLM_EMBED_MODEL =
gemini/gemini-embedding-001`, `LITELLM_LLM_MODEL = gemini/gemini-3.5-flash`,
`LITELLM_JUDGE_ROUTE_MODEL = openrouter/nvidia/nemotron-3-super-120b-a12b:free`.

**Solution.** Two acceptable outcomes, one unacceptable:
1. Swap to a self-hosted embedding model — LiteLLM already abstracts the route,
   so **no code changes**, but a full re-index is required; or
2. Record written acceptance of the egress.

Silence is the unacceptable one. Documented on the diagram and slides 2026-09-14;
the decision itself is still open.

---

### A3 · No TLS between services · `OPEN` · register #31

**Flaw.** Service-to-service traffic is plaintext HTTP.

**Why.** Deferred deliberately for a single-host deployment.

**Solution.** TLS/mTLS. Required before the system leaves a trusted LAN;
unnecessary while it does not.

---

## Group B — Green lights that mean nothing

> The most dangerous group. Each of these *reports success* while telling you
> nothing, which is worse than reporting failure.

### B0 · `wiki/Concepts` and `wiki/concepts` both exist · `DECIDED` · register #60

**Gates B2, which gates B1.** Promoted out of Group D on 2026-09-14 because the
lowercase-directory rule chosen for B2 cannot be written while the vault violates
it.

**Flaw.** Two directories differing only in case. Works on Linux; on a
case-insensitive filesystem (macOS, Windows) a clone merges or fails.

**Measured 2026-09-14.**

| | |
|---|---|
| `wiki/Concepts` | 16 files |
| `wiki/concepts` | 47 files |
| **name collisions between them** | **0** — nothing would be overwritten |
| references to `Concepts/` in the vault | **15** |
| of the 16 pages, present in `wiki/index.md` | 10 |

**Solution — four steps, in order:**

1. `git mv` each of the 16 files into `wiki/concepts/`
2. **Rewrite the 15 `Concepts/` references** — in `index.md`, `log.md` and page
   bodies. A bare `git mv` leaves them dangling.
3. Regenerate `wiki/index.md`
4. Push, and watch the ingest cycle

**Step 4 exercises a path never used on real content.** `source_uri` changes for
all 16 pages, so the old rows are **purged and re-inserted**, not updated. The
`cycle_complete` event should report `deleted_count: 16`. If it reports 0, the
purge path did not run and the index now holds 16 orphans under the old URI.

**Filesystem hazard, and it is this defect appearing inside its own fix:** on a
case-insensitive filesystem `git mv Concepts/X.md concepts/X.md` can be a no-op
or a case-only rename Git ignores. The portable form is two steps —
`git mv Concepts tmp && git mv tmp concepts`. Safe on Linux; required anywhere
else.

---

### B1 · `verify-vault` is inert to every input · `DECIDED` · register #59

**Flaw.** Identical output regardless of where it runs or how it is configured.

**Measured 2026-09-14**, run from inside `~/vault-edit` (434 pages):

| invocation | result |
|---|---|
| no env | `7 pages · 0 errors · PASS` |
| `WIKI_DIR=~/vault-edit/wiki` | `7 pages · 0 errors · PASS` |
| `WIKI_DIR=/tmp` | `7 pages · 0 errors · PASS` |

It always lints the installed package's own 8-page sample tree.

**Why.** `scout/vault.py:21` sets `WIKI_DIR = REPO_ROOT / "wiki"` where
`REPO_ROOT` derives from `Path(__file__)` — the *source file's* location.
`verify_vault` calls `cfg.require_repo()` and discards the result.

**Why it survived.** All four tests in `tests/test_cli_verify.py` monkeypatch
`_pages_and_lint` — the function holding the defect — and assert only on exit
codes. They pass identically whichever tree is linted.

**Also affected:** `verify-addresses`. From the system repo it checks 5 addresses
(the sample tree); from the vault it fails on `POSTGRES_QUERY_PASSWORD_FILE`
resolving relatively. The fault is the verify *family*, not one command.

**Solution.** Resolve from `cfg.require_repo()`, following the correct pattern
already in `scout/cli/commands/wiki.py:91`. **Plus a test that lints a tree
outside the package** — without it the fix is unverifiable by the same suite that
missed the bug.

**Coupled to B2.** Fixing this makes the linter read the real vault, which
immediately surfaces B2's four errors. Do them together or the fix only produces
noise.

---

### B2 · The lint's tree contract does not match the vault · `DECIDED` · register #61

**Flaw.** On the real 434-page vault the linter reports 4 errors, all *missing
required tree entry*: `wiki/archive.md`, `wiki/techniques`, `wiki/entities`,
`wiki/playbooks`. The vault actually uses `concepts/`, `queries/`, `Security/`,
`Entities/`, `Tools/`, `Sources/`, `comparisons/`, `Web Clips/`.

**Why.** The contract encodes a layout the vault never adopted. This is the
concrete shape of `CLAUDE.md`'s own warning that *"the current automated checker
is narrower than the complete V3 page contract."*

**Decided 2026-09-14: the contract is wrong; the vault layout stays.**
All 434 pages parse successfully — only the tree assertion fails.

**Measured breakdown of the four errors** — they are not the same problem:

| required entry | reality | fix |
|---|---|---|
| `archive.md` | genuinely absent | drop from the contract |
| `techniques` | genuinely absent | drop from the contract |
| `playbooks` | genuinely absent | drop from the contract |
| `entities` | **vault has `Entities`** | **case mismatch, not absence** — see below |

**Present but unrequired:** `Concepts` `Security` `Sources` `Tools`
`comparisons` `queries` `raw` `Web Clips` `SCHEMA.md`. The vault's real taxonomy
is wider than the contract and uses mixed case.

**The `entities` row couples B2 to D1.** The vault carries both `Concepts` and
`concepts`, and the contract wants lowercase `entities` where the vault has
`Entities`. Deciding the contract therefore forces a decision on case — and a
case-insensitive contract would silently accept the `Concepts`/`concepts`
duplication that D1 exists to remove.

**Decided 2026-09-14 — the contract becomes a rule, not a list:**

> Required: the control files `index.md`, `log.md`, `SCHEMA.md`.
> Plus: **every directory under `wiki/` is lowercase.**

A fixed list of content directories is what drifted in the first place; re-pinning
it to today's nine would drift again on the next category. A rule does not.

**This requires B0 first.** The rule cannot be written while `wiki/Concepts`
exists — the vault would fail its own contract on day one.

---

### B3 · RRF discards the relevance signal · `OPEN` · register #66

**Flaw.** `wiki_search` never returns empty, and nothing downstream can tell
"best match" from "best of nothing". The system cannot say *"not in the vault"*
on its own — it relies on the read step catching it, which is probabilistic when
the question is topically vague.

**Why.** The exposed `score` is the RRF score, which is rank-based by
construction — `1/(60+rank)`. A good query's rank-1 scores 0.0328; a nonsense
query's rank-1 scores 0.0156. **Both are "rank 1".** The cosine magnitude that
knows the difference is computed in SQL and thrown away.

**Measured 2026-09-14**, best cosine over 2,070 chunks:

| query | best cosine |
|---|---|
| OpenShift CVE (in corpus) | **0.804** |
| MCP servers (in corpus) | **0.726** |
| trà sữa trân châu | 0.522 |
| payroll process | 0.545 |
| football fixtures | 0.505 |

Clean separation: 0.73–0.80 against 0.50–0.55.

**Solution.** Return the raw cosine alongside the RRF score; apply a floor around
0.65. **Re-measure all 40 benchmark questions before settling the threshold** —
a floor that silently costs recall is worse than no floor.

---

### B4 · Gitleaks has never scanned a commit · `OPEN` · register #26

**Flaw.** `.gitea/workflows/security.yaml` has never produced a scan.

**Why.** It mounts `$GITHUB_WORKSPACE/trusted-security/.gitleaks.toml` — a path
that does not exist, and a *container* path resolved by the *host* daemon.
Docker created it as an empty directory, so gitleaks received a directory where
it expected a config.

**Not a security hole.** `scripts/scan_secrets.py` scans all-ref history and
passes; an independent gitleaks run over 177 commits found nothing. The workflow
is wrong-and-off, and will one day be turned on and trusted.

**Solution.** Run the static binary instead of Docker — that deletes the mount
rather than fixing it. Folds into the CI container work (C4).

---

## Group C — Architecture and vocabulary

### C1 · `scout` and `snpmemory` each mean several things · `DECIDED`

**Flaw.**

```
  scout      python module scout/ · MCP server name · image snp-scout
  snpmemory  CLI binary · local MCP server name
  sync-job   the compose service runs the snp-scout image
```

Neither word can be used unambiguously. This caused a full session of
back-and-forth in which the owner and I each meant different things by "scout".

**Solution — decided 2026-09-14:**

| | |
|---|---|
| MCP server | **`snpmemory`** (the CLI's name, freed by C2) |
| Tools | `wiki_search` `wiki_read` `wiki_quote` `wiki_verify` `wiki_plan` `wiki_trace` `stack_health` |
| Rendered | `snpmemory_wiki_search`, `snpmemory_stack_health` |
| `scout` | survives only as the Python module — invisible outside the repo |

**Conforms to SEP-986** (Final): 1–64 chars, snake_case, `_` permitted. The
domain prefix sits **in the tool name, not only in the server name**, because
`{server}_{tool}` is a convention and *not a collision boundary* — a client that
does not namespace by server would otherwise expose a bare `read`.

**Migration.** SEP-986 requires old names to survive as aliases for at least one
major version with a deprecation warning. `_RETIRED_SERVER_NAMES` already exists
but only *removes* keys — it needs an alias phase. **And a type-aware rule:**
`snpmemory` currently names a *stdio* server being deleted; a stale config entry
would shadow the new remote one.

---

### C2 · The CLI is a 26-command product nobody uses · `DECIDED`

**Flaw.** 25 of 26 commands declare `Prerequisite.LOCAL` — *"needs a repository
checkout on disk"*. `Prerequisite.REMOTE` exists in the enum, is documented, and
is used by **zero** commands. The CLI was never a remote surface, while the
product was packaged as though agents anywhere could use it.

**Measured.** Of 26: 13 work, 2 broken, 2 degraded, 9 untested (destructive).
Across the entire rehearsal and demo the agent called **only** `scout_wiki_read`
(25×) and `scout_wiki_search` (8×). Zero CLI calls.

**Solution — decided.** Remove the CLI's *identity as a product*, not its code.
Every command lands in exactly one destination:

```
  agents  →  MCP tools        7
  CI      →  a container      5   (consolidate the 28 scripts/ first)
  skills  →  ships WITH the skill, archify-style   4
  operator, deferred                               3
  client setup                                     2
  dropped                                          6
                                                  ──
                                                  27  (26 commands + 1 new tool)
```

### C2a · The MCP surface — 7 tools

| Tool | Converted from | Purpose |
|---|---|---|
| `wiki_search` | CLI `search` | Rank distinct vault pages through the shared pgvector index. Returns page identities and bounded routing snippets — never answer text. |
| `wiki_read` | CLI `read` | Read a canonical page envelope from the published snapshot. Modes `tldr` → `outline` → `section` → `full`. **This is where answers come from.** |
| `wiki_quote` | CLI `fetch` | Resolve one `sources[]` address to its verbatim passage and citations. **Currently hidden** — without it an agent can cite compiled pages but can never check one against its sources. |
| `wiki_verify` | **NEW — not a conversion** | Check *one draft page*, content-in, against the schema and heading frame. Deliberately **not** `verify-vault`: whole-vault health belongs to CI (C4). An agent checks its own work before pushing; it never audits the corpus. |
| `wiki_plan` | CLI `plan-articles` | Propose a multi-article decomposition from a source's own headings. Deterministic — no model call, no database, no stack. The most remote-able operation in the system. |
| `wiki_trace` | **NEW — not a conversion** | Answer *"is my page indexed, when?"* and *"what happened to commit X?"*. Replaces `scripts/demo/watch-sync.sh`, which was written during the demo because no tool existed. See `OBSERVABILITY_2026-09-14.md`. |
| `stack_health` | CLI `status`, **partially** | Report snapshot commit, documents indexed, last ingest, embedding coverage, recent failures, CI verdict. **Drops the docker half** — container health, image revision and DNS need the docker socket, which a server must not have. Those stay operator work. |

Two are new capabilities rather than conversions, and two are deliberately
*narrower* than the command they came from — `wiki_verify` sheds whole-vault
scope, `stack_health` sheds docker.

### C2b · The CI surface — 5 commands, two triggers

**Decided 2026-09-14: free checks block a push, expensive ones run weekly.**
Split by cost, not by family. Precedent: the retired `auto-healer.yaml` ran
`on: pull_request` **plus** `schedule: cron '0 0 * * 0'`.

| Command | CI stage | Purpose | Needs | Trigger |
|---|---|---|---|---|
| `verify-vault` | `vault` | Lint page frontmatter and confirm `wiki/index.md` is current | nothing | **every push** |
| `verify-secrets` | `secrets` | Scan tracked, staged and untracked bytes for credential-shaped values | nothing | **every push** |
| `verify-addresses` | `addresses` | Check that every page's `sources[]` hint still retrieves its own file | LiteLLM + Postgres | **weekly** — one embedding per address |
| `verify-groundedness` | `groundedness` | Judge each page's body against the sources it cites | LiteLLM judge | **weekly** — one judge call *per page* |
| `check` | `all` | Run every verification in order, stop at the first failure | the above | weekly |

**Why the split.** `verify-groundedness` costs one judge call per page — 434 calls
per run. On every push that is unaffordable, and it is also the command that
**currently times out at 90 s with no output** on its OpenRouter judge route. It
enters the plan as *CI-scheduled, unproven*, not as working.

The two free checks block a push because they are the ones that must never lag:
schema drift and leaked credentials.

Results are written as SARIF into `health_reports` (C4) and surfaced to agents
through `stack_health`, never by running the check on demand.

### C2c · Everything else

| Destination | Commands | Why |
|---|---|---|
| **skills** (4) | `compile-plan` `compile-status` `compile-cancel` `logs` | write into a checkout, or need the docker socket |
| **operator, deferred** (3) | `up` `down` `init` | lifecycle and credential generation; decision parked |
| **client setup** (2) | `install-agent` `mcp-config` | write into a directory the user owns |
| **dropped** (6) | `compile` `mint` `propose` `ingest` `ingest-wiki` `schema` `mcp` | redundant, superseded, or forbidden by R-7.3 |

The archify precedent for the skills: `bin/archify.mjs` lives inside the skill,
is invoked by relative path, depends only on language builtins, and is installed
nowhere. The vault lint is a viable candidate — `scout/vault.py` needs only
stdlib plus `yaml`.

---

### C3 · Two MCP servers, same tool names, different trees · `DECIDED` · register #65

**Flaw.** Both servers exposed `wiki_search`/`wiki_read`. Scout reads the
published snapshot; the local server read the **uncommitted working copy**.
Nothing in any shipped skill or rule said which to prefer, so an agent could
answer from unpushed text while appearing to speak for the shared vault.
Reachable by default on the `mcp-config --client claude` path.

**Solution — decided.** Delete the local server. Local `wiki_search` was Scout's
search with a *self-asserted* department instead of a token-issued one — strictly
weaker. Local `wiki_read` duplicated the agent's own file tool.

**The flaw is removed, not mitigated.** There is no second surface to choose
between.

---

### C4 · Vault health has no owner · `DECIDED`

**Flaw.** Nothing checks the vault on a schedule. CI cannot run at all —
`actions/runners` returns `total_count: 0`, and `checks.yaml` checks *code*
(ruff/mypy/pytest), not the vault.

**Solution — decided.** Vault health becomes a **CI job**, never an agent tool.
CI runs a container; results are written as **SARIF** into a `health_reports`
table; `stack_health()` reports the verdict and its age.

SARIF because it is the settled 2026 standard for findings and GitLab consumes it
natively, so the A1 migration needs no rework. **OpenTelemetry CI/CD semantic
conventions were considered and rejected:** Release Candidate status, they answer
*"how long did the pipeline take"* rather than *"is the vault healthy"*, and they
require a collector and backend for a single-box system with zero runners.

---

### C5 · `raw/` means two different trees · `OPEN` · **new 2026-09-14**

**Flaw.** `source_uri` beginning `raw/` refers to two unrelated trees:

```
  101 documents  →  the vault's  wiki/raw/   (Git lane, in the replica)
    2 documents  →  the host's   raw/        (bind mount, NOT in the replica)
```

**Consequence — measured.** Those 2 are **searchable but not readable**.
`wiki_search` returns them; `wiki_read` cannot open them, because Scout reads the
replica and they were never published there. The URI gives no warning.

**Why.** Two ingest lanes write into one URI namespace. The host corpus lane was
added without a distinct prefix.

**Solution.** Give the host corpus its own prefix (`corpus/…`), or exclude it
from search until it is readable. **This is the only naming twin with a measured
wrong result** — the rest are readability debt.

---

### C6 · Synonym drift across the codebase · `DECIDED`

**Flaw.** One concept, several spellings: `department` (70) · `departments` (58)
· `dept` (50) · `depts` (5). Also `vault` (17 files) against `wiki` (15).

**Why.** No shared glossary; each author picked a spelling.

**Solution — decided.** A `GLOSSARY.md` of roughly a dozen terms, **plus a lint
rule that fails on rejected synonyms in new code.** A glossary without
enforcement decays; that is the documented failure mode.

Two constraints:
- **Do not rename existing code.** No behavioural payoff, touches everything.
  Freeze drift going forward; fix opportunistically.
- **`vault` versus `wiki` cannot be resolved.** The directory is `wiki/`, the
  tools are `wiki_*`, the owner says "vault". The glossary should rule:
  **`wiki` in code and tool names, `vault` in prose.**

---

### C7 · `GateFailure` defined in 12 files · `DECIDED`

**Flaw.** The same exception class is copy-pasted across 12 files in
`artifacts/v3/checks/`. Also duplicated: `Question` (2 — nearly identical,
`lang/query/expect`), `Config` (2 — genuinely different), `Finding` (2 —
genuinely different).

**Why.** No shared module for check infrastructure.

**Solution — decided.** Consolidate `GateFailure` into one shared module.
`Question` too — it is one concept with two definitions. Leave `Config` and
`Finding`: same name, genuinely different shapes, different modules.

---

## Group D — Contained

| # | Flaw | Status | Solution |
|---|---|---|---|
| D2 | Installer test encodes a superseded contract (now refuses a non-existent target) | `OPEN` #64 | update the test, or restore directory creation — owner's call |
| D3 | `install-agent` writes no entry file for claude/gemini/cursor (opencode gets `opencode.json`) | `OPEN` #27 | write the client's entry file |
| D4 | `basic-memory/` dead but load-bearing, 292 KB | `OPEN` #32 | coherent 4-file retirement |
| D5 | Vietnamese recall 80% against English 95% | `OPEN` #39 | word segmentation, or a Vietnamese-strong embedding model |
| D6 | Obsidian editing chosen but not set up | `OPEN` #33 | Obsidian Git plugin |
| D7 | `.unlazy/` gitignored — a destructive edit is unrecoverable | `OPEN` #40 | copy before scripted edits, or track it |
| D8 | Lock service never built | `OPEN` #30 | only matters with concurrent writers |

---

## Resolved

| # | Flaw | Evidence |
|---|---|---|
| #28 | `opencode` missing from `SUPPORTED_CLIENTS` | now `["cursor","vscode","claude","gemini","opencode"]` |
| #38 | 86% of the vault had no `## TL;DR` | **162/434** after batch-3b landed, recall unchanged |
| #65 | Two servers, twin tool names | removed by decision C3 — not mitigated, deleted |

---

## Order of work

| Phase | Contains | Why first |
|---|---|---|
| **P0** | **B0 → B2 → B1**, in that order · then C7 · C6 glossary | no decisions left; deletes the misleading greens |
| **P1** | C5 `raw/` namespace · B3 relevance floor | the two with measured wrong behaviour |
| **P2** | C1 rename · C2 CLI dissolution · C3 already done | vocabulary; breaks configs, needs an alias period |
| **P3** | Observability O1–O5 (see `OBSERVABILITY_2026-09-14.md`) | new capability; O1/O2 need no migration |
| **P4** | C4 CI container · B4 gitleaks | needs a runner to exist |
| **P5** | A1 GitLab · A2 egress decision · A3 TLS | the handover gate |

**P0 needs no decisions from anyone and removes the flaws that actively lie.**
Start there.

**The order inside P0 is not free.** Each step makes the next one expressible:

```
  B0  merge Concepts → concepts        the vault stops violating the rule
   └─ B2  contract: control files + "directories are lowercase"
       └─ B1  verify-vault reads the caller's checkout, and the
              contract it checks against is now true
```

Reversed, B2's rule fails on a vault that still has `Concepts`, and B1's fix
surfaces an error the contract cannot yet express.

---

## Related documents

- `docs/DEFECT_REGISTER_2026-09-09.md` — historical log, entries #1–#67
- `docs/proposal/CLI_MCP_VOCABULARY_2026-09-14.md` — the rename table
- `docs/proposal/OBSERVABILITY_2026-09-14.md` — `wiki_trace` and `stack_health`
