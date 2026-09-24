# Defect register — everything found, and what happened to it

**Compiled:** 2026-09-09, from the 2026-09-07 → 2026-09-09 sessions.
**Status key:** ✅ fixed and verified · ⏳ open · 🚫 deliberately not fixed · ❓ cause unknown

---

## 1 · Code defects — fixed

| # | Defect | Why it mattered | Commit |
|---|---|---|---|
| 1 | `wiki_search` returned a bare list | Agent could not tell a complete result from a truncated one, nor spot pages redacted as already-seen | `e4a7467` |
| 2 | `has_more` was `len(hits) == k`, true at `k=0` | Empty result claimed more pages existed. `k` unvalidated on both endpoints | `04344ed` |
| 3 | `_SEARCH_OUTPUT_SCHEMA` omitted `title` and `reason` | Declaring the schema **deleted** those fields from `.data` client-side — stripping the very field naming a redacted page | `04344ed` |
| 4 | Three MCP-client gate consumers broken by schema coercion | `_Scout.search` returned `[]` for every query **silently**, so the W-2 acceptance gate could only time out | `b701451` |
| 5 | `_scout_surface_errors` asserted the auto-derived wrap schema | The `contract-matches-surface` gate failed against the real declared schema | `ad7bb4c` |
| 6 | host-sync had no `depends_on: git` | Initial sync raced Gitea's 40 s boot and lost; error then persisted in memory forever | `fbc8244` |
| 7 | host-sync had no retry | One transient failure left `/ready` degraded until an unrelated webhook happened along | `3cc7ad8` |
| 8 | `_IngestConnection` fake missing `fetch` | `--group full-chain` had **never passed** in project history. Fake deleted; gate now uses real PostgreSQL | `25360fd` |
| 9 | stdio server returned a bare list while HTTP returned an envelope | Two servers disagreed on a same-named tool's shape | `2c479d1` |
| 10 | `load_pairs(path=EVAL_XML)` bound its default at import | Overrides were no-ops — produced a **false PASS** in a control designed to catch false passes | `a1aebe4` |
| 11 | Guard substring `auto-heal` collided with live `auto-healer.yaml` | False positive that would have been silenced with an escape hatch covering real staleness | `1e2bfec` |
| 12 | Guard caught only empty `[]`, not populated arrays | The likelier form of the same defect sailed past | `1e2bfec` |
| 13 | Image labels: wrong repo URL, `revision: unknown` | Deployment could not attest to what it was running | `9f135cb` |
| 14 | Same wrong URL in `Dockerfile.sync`, `basic-memory/Dockerfile` | Found by repo-wide grep beyond the brief | `9f135cb` |
| 15 | `install-agent` additive on upgrade | Old skill dirs survived a rename — nine skills, four retrieval skills, two named after a retired tool | `8858658` |

## 2 · Logic and documentation defects — fixed

| # | Defect | Commit |
|---|---|---|
| 16 | Package said `## TL;DR` "recommended" while code **required** it; `## Provenance` "required when sources declared" while unconditionally optional | `8e0a058` |
| 17 | `HeadingFrame` COMPILED/AUTHORED existed in code, taught nowhere | `f9f960f` |
| 18 | `plugin.json` repository URL pointed at a nonexistent repo | `a487709` |
| 19 | Retrieval skills named backwards — `snp-search-wiki` was the READ skill; `snp-rag-fetch` named after a tool V3 retired | `d2cca03` |
| 20 | Runbook: `/health` 404s, `--print` does not exist, unguarded `git push origin main` where origin is a **public** repo | `88a1b4a` |
| 21 | Runbook: `tldr` read then cited a heading `tldr` cannot supply; claimed 3–5 s against measured ~7 s; Act 4 had the agent pushing, which no tool can do | `0017606` |
| 22 | Scorecard shipped ten `[ PASS ]` for a rehearsal that never happened | `0017606` |
| 23 | README / DEMO / ARCHITECTURE_STATUS described `basic-memory`, `rag_fetch`, FastEmbed @384 — a system V3 deleted | `688f961` |
| 24 | `docs/DEMO.md` contradicted itself in its first five lines | `eba965d` |
| 25 | I-3 unmeasurable — ambiguous start point, no sample size, no conditions | Codex, `5ebbc49` |

---

## 2b · Code defects — fixed 2026-09-13

| # | Defect | Why it mattered | Commit |
|---|---|---|---|
| 52 | **The sparse arm never matched anything** | `plainto_tsquery` AND-joins every lexeme, so a multi-word question needed all its stems in one chunk. 35 of 40 benchmark queries matched **zero** rows; sparse-only recall@1 was 0.10 / vi 0.00. The documented "provider failure degrades the query" path was a placebo, and all of the measured 0.88 came from the dense arm alone — a single point of failure on the embedding provider. Fixed as two tiers: relaxed disjunction when dense is absent, strict conjunction (unchanged) in hybrid. | `9761f6f` |
| 53 | **`curl` pin expired, host-sync could not build** | `curl=8.14.1-2+deb13u4` stopped resolving (apt exit 100) after Debian published deb13u5 and dropped the superseded build. Predicted in the Dockerfile's own comment; failed loudly as designed. Refreshed within the same upstream version. `snapshot.debian.org` remains the durable fix and an acceptance criterion for the clean release build. | `88b84f1` |
| 54 | **5 mypy errors in `scripts/rehearsal_preflight.py`** | Violated the repo's strict-clean constraint. Includes one real typing hazard: the MCP client factory did not satisfy `McpHttpClientFactory`. Fixed without changing behaviour — the factory still absorbs and overrides caller kwargs, which a security test asserts. Uncommitted, riding with Codex's in-flight rehearsal work. | working tree |
| 55 | **104 ruff errors + 1 format failure** | All in untracked scratch: `.runtime-feature-audit/` probes (now gitignored) and an authored V4 proposal whose fenced Python ruff would rewrite (`docs/proposal/` now excluded, matching the existing `wiki/` rationale). | `9761f6f` |

## 3b · Measurement findings — 2026-09-13

| # | Finding | Detail |
|---|---|---|
| 56 | **Hybrid's 0.88 was measured against a dead lexical arm** | Repairing the arm and leaving it an equal RRF voter *cost* recall: en 0.95 → 0.85, vi 0.80 → 0.60, while recall@3 held at 1.00 — the right pages were found, the ordering was damaged. A per-arm RRF penalty was tried and rejected: adjacent RRF ranks differ by ~1/61 vs ~1/62, so any comparable second signal reshuffles rank 1, and a constant large enough to stop it silences the arm. The fusion weighting has never been tuned against a working sparse arm; that is open work, not a defect. |
| 57 | **Vietnamese is syllable-segmented, and no lexical mechanism fixes it** | `'simple'` strips no stop words and Vietnamese compounds split per syllable ("mô hình" → `'mô'`+`'hình'`). Measured: the discriminating proper noun `tailscale` has document frequency 24, no rarer than `máy` 20 or `chủ` 22, so IDF weighting cannot separate them; heading weighting lifted the target only to rank 2 behind a legitimately topic-adjacent page. Sparse-only vi recall@1 therefore plateaus at 0.25. Dense retrieval is what answers paraphrased Vietnamese, which is an argument for keeping it, not for replacing it. |
| 58 | **One page, not a rule: a capability page outranks its tool page** | Batch 3b reverted twice with identical numbers (en 0.90 / vi 0.75 / ALL 0.82). My first diagnosis — that the cause was a category page naming its own exemplar — was **wrong**, and I recorded it here before testing it. Agy removed `DrawDB` from that chunk 0 in revision 2 and the query failed identically. A single-variable test (same tree, TL;DR removed from that one page only, 1 indexed / 429 unchanged) restored the full baseline 0.95 / 0.80 / 0.88, so **all** of the drop comes from `concepts/Online ERD & SQL Generator.md` and the other 39 pages are neutral or positive. Mechanism: once the concept page has any chunk 0, it reads as a restatement of the query itself ("browser ERD tool that generates SQL DDL"), so a capability-phrased question matches the capability page over the tool page. Whether that is a vault regression or a too-narrow benchmark label is an owner judgement; `recall@3` is 1.00 either way. Owner chose 2026-09-13 to land 39 pages and omit that one TL;DR, changing no benchmark. |

| 62 | **The "live" architecture diagram omitted a live ingest lane — then described it wrongly** | 431 of 433 indexed documents arrive through the Git path (push → webhook → host-sync → replica → sync-job). The other 2 arrive through the **`raw/` corpus**, which the 2026-09-13 diagram omitted entirely. **The first correction was also wrong:** it drew the lane as an operator running `snpmemory ingest` by hand. Measured 2026-09-14 — `docker compose config` shows `source: <repo>/raw → target: /data/raw, read_only: true`, and sync-job's own logs carry `/data/raw: cold-start sync`, so **sync-job watches `raw/` exactly as it watches `wiki/`**. Nobody runs a command; a person copies a file into `raw/` on the host and the watcher picks it up. `snpmemory ingest` exists as a manual re-index but is HIDDEN from MCP precisely because "the sync-job already indexes raw/ on a watch" (`mcp_policy.py`) — the reason string said so all along and I did not read it. ACLs come per-file from `raw/.acl.yaml` rather than the vault default of `all`, which is why both narrow-ACL documents live on this lane. Side effect worth knowing: a `raw/` document is searchable but **not readable** through `wiki_read`, because it is not in the replica. Diagram and slide 6 corrected 2026-09-14. |
| 63 | **RLS measured end to end for the first time** | `scripts/demo/rls_scope.sql` under `SET LOCAL ROLE rag_app_role`: `infra` sees 432 documents, `redteam` 431, `ai_eng` 432, and an **empty** `scout.current_depts` sees **0**. The fail-closed clause is the policy's own `NULLIF(current_setting('scout.current_depts', true), '') IS NOT NULL`. Previously asserted from code reading; now observed. |

## 3 · Open — code

| # | Defect | Detail |
|---|---|---|
| ⏳ 26 | **Gitleaks has never scanned a commit** | `.gitea/workflows/security.yaml:47` mounts `$GITHUB_WORKSPACE/trusted-security/.gitleaks.toml` — a container path resolved by the **host** daemon, and the file lives at `.gitleaks.toml` anyway. Docker created it as an empty directory. **Not a security hole:** `scripts/scan_secrets.py` already scans all-ref history and passes, and an independent gitleaks run over 177 commits found nothing. The workflow is wrong-and-off; it will one day be turned on and trusted. |
| ⏳ 27 | **`install-agent` writes no entry file** | No `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`. One of **two** independent reasons a clean OpenCode install sees zero skills. |
| ⏳ 28 | **No `opencode` in `SUPPORTED_CLIENTS`** | `['cursor','vscode','claude','gemini']`. The demo client is the one not wired. The other half of 27. |
| ✅ 65 | **Two MCP servers expose the same two tool names over different trees, with no rule for choosing** | `scout` serves `wiki_search`/`wiki_read` from `/vault-replica/current/wiki` — the **published snapshot**. `snpmemory` (the CLI over stdio) serves the same two names from `cfg.require_repo()/wiki` — the caller's **uncommitted working copy**. Wire both and an agent sees two tools per name reading different content. `packages/snp-agent/instructions/agent_guide.instructions.md:7` acknowledges the overlap — *"The local `snpmemory` server exposes the same retrieval contract"* — and then gives **no rule for which to prefer**; no shipped skill, rule or instruction names a server at all. **Consequence:** an agent can answer from unpushed local edits while appearing to speak for the shared vault, which silently breaks the "index finds, vault answers" invariant — the answer no longer comes from what the team can see. **Reachability, measured 2026-09-14:** `install-agent --client opencode` and `mcp-config --client opencode` wire scout only (safe); **`mcp-config --client claude` wires both**, and `packages/snp-agent/mcp.json` — the plugin-install path — declares both. So the collision is the default on the Claude path, not a hypothetical. **Why it survived:** `tests/test_export_mcp_config.py:25` asserts `set(servers) == {"scout", LOCAL_SERVER_NAME}` — the test *enforces* both being configured — and the only client ever exercised with a live agent was OpenCode, whose path wires scout alone. Config **shape** was tested; agent **behaviour with both wired** never was. Not reachable in the 2026-09-14 demo: both workspaces wire scout only, verified twice. **Fix options (owner):** (a) rename the local tools (`vault_search_local`), (b) drop retrieval from the local server since Scout already serves it, or (c) add an explicit precedence rule to the shipped rules file. (b) is smallest and matches the "one door" invariant. **Fixed 2026-09-24 (leaf-4.3/4.4), beyond option (b):** the local server is deleted outright rather than having retrieval removed from it, so there is no second server to choose between and no precedence rule to write. The test that enforced both being configured now asserts one. The remaining server then took the freed name `snpmemory`, and `mcp-config` removes the superseded `scout` entry instead of leaving both. Verified by `local_server_gone.py`, `stale_config_corrected.py` and `server_named.py`. |
| ⏳ 66 | **RRF discards the one number that knows a query has no answer** | `wiki_search` never returns empty — kNN always has a nearest neighbour, so an out-of-corpus question still gets 5 confident-looking pages. That is normal for similarity search, and the two-stage contract ("the index finds, the vault answers") is the correct architectural response: `wiki_read` opens the page and the agent reports the absence. **But the separation is measurable and currently thrown away.** Measured 2026-09-14, best cosine over 2,070 chunks: in-corpus questions 0.804 (OpenShift CVE) and 0.726 (MCP servers); out-of-corpus 0.522 (trà sữa), 0.545 (payroll), 0.505 (football fixtures). Clean gap, 0.73-0.80 vs 0.50-0.55; a floor at ~0.65 separates every sample. The exposed `score` field is the **RRF score**, which is rank-based by construction (`1/(60+rank)`): a good query's rank-1 scores 0.0328 and a nonsense query's rank-1 scores 0.0156 — both are "rank 1", and the number reports position, not relevance. So the cosine magnitude exists at the SQL level and is discarded before any consumer can threshold on it. **Consequence:** nothing downstream can distinguish "best match" from "best of nothing"; the system cannot say "not in the vault" on its own and depends entirely on the read step catching it, which is probabilistic when the question is topically vague. **Fix:** carry the raw cosine alongside the RRF score, apply a floor around 0.65, and re-measure all 40 benchmark questions before settling the threshold so it cannot silently cost recall. Deliberately not attempted before the 2026-09-14 demo — changing retrieval scoring hours before a demo risks arriving with something worse. |
| ⏳ 67 | **The architecture never documented that vault text leaves the network** | Every diagram, slide and runbook drew LiteLLM as an internal gateway and stopped there. Measured 2026-09-14 inside the running container: `LITELLM_EMBED_MODEL = gemini/gemini-embedding-001`, `LITELLM_LLM_MODEL = gemini/gemini-3.5-flash`, `LITELLM_VLM_MODEL = gemini/gemini-3.5-flash`, `LITELLM_JUDGE_ROUTE_MODEL = openrouter/nvidia/nemotron-3-super-120b-a12b:free`. So **page content egresses to Google on every ingest, and every question egresses to Google on every search**; authoring additionally reaches OpenRouter. Not a defect in behaviour — it is the configured design and the owner knows the API keys exist — but it was **absent from every artifact describing the system**, including a diagram whose security boundary is captioned "only one door in". The first person to ask "where does my data go?" would have been told something incomplete by the drawing. Found only because the owner asked for a full input/output audit of the diagram. Fixed 2026-09-14: card on the live diagram, warning block on slide 4, entry in the not-finished list on slide 20, appendix row citing the env values. **Open decision:** a self-hosted embedding model removes the egress and costs nothing in code (LiteLLM already abstracts the route) but requires a full re-index. |
| ⏳ 59 | **`snpmemory verify-vault` is inert to every input** | `scout/cli/commands/verify.py:46` passes the module constant `vault.WIKI_DIR` (`scout/vault.py:21`, derived from `Path(__file__).parents[1]`) after calling `cfg.require_repo()` and discarding the result. **Control-tested 2026-09-14** from inside `~/vault-edit` (433 pages): no env, `WIKI_DIR=/home/ple/vault-edit/wiki`, and `WIKI_DIR=/tmp` all produce byte-identical output — `7 pages · 0 errors · 2 warnings · index current — PASS`. It always lints the installed package's own 8-page sample tree. A green light that cannot be made red by any input is not evidence. **Why it survived:** all four tests in `tests/test_cli_verify.py` monkeypatch `_pages_and_lint` itself — the function holding the defect — and assert only on exit codes and result shape, so they pass identically whichever tree is linted. **Correction to the first version of this entry: `snpmemory read` is NOT affected.** `scout/cli/commands/wiki.py:91` resolves `cfg.require_repo() / (WIKI_DIR or "wiki")` with an escape guard, which is the correct pattern this fix should copy. My earlier failing `read` test was run from the system repo's directory and so correctly served the system repo's vault; the test was wrong, not the code. Blast radius of the fix: one function, ~5 lines; `gen_index.write_mode_allowed` already accepts a `wiki_dir` override. Nothing in scripts, gates, CI or compose depends on this command's exit code. |
| ⏳ 60 | **The vault carries both `wiki/Concepts` and `wiki/concepts`** | Two directories differing only in case. Works on Linux; collides on any case-insensitive filesystem, so a macOS or Windows clone of the vault will merge or fail. Found 2026-09-14 while linting the real tree. |
| ⏳ 64 | **The rehearsal's 506-test regression run excluded a test of the script it changed** | Codex's `regression-tests.json` names 10 test files and reports 506 passed, exit 0 — accurate and correctly scoped in the sense that it lists what it ran. But `scripts/install-agent.sh` **is** one of the changed files, and `tests/test_agent_package.py` — which drives that installer — is not in the list, while the neighbouring `test_cli_install_agent.py`, `test_install_opencode_agent.py` and `test_agent_package_sync.py` are. The full offline suite, run 2026-09-14, shows `test_agent_package.py::test_installer_nested_path` failing: the installer now refuses a non-existent target (`Target must be an existing directory`, exit 3) while that test asserts it creates a deeply nested path. **The behaviour change is defensible** — refusing to `mkdir -p` an arbitrary typo'd path is safer — but the contract change was never reconciled with the test that encodes the old contract, and the scoped run could not have seen it. Owner decision: update the test to the new contract, or restore directory creation. Demo impact, found and fixed 2026-09-14: `snpmemory install-agent <dir>` fails identically on a missing directory, so the playbook now says `mkdir -p` first. |
| ⏳ 61 | **The vault lint's tree contract does not match the vault** | `check_tree` on the real 433-page vault reports 4 errors, all *missing required tree entry*: `wiki/archive.md`, `wiki/techniques`, `wiki/entities`, `wiki/playbooks`. The vault actually uses `concepts/`, `queries/`, `Security/`, `Entities/`, `Tools/`, `Sources/`, `comparisons/`, `Web Clips/`. All 433 pages parse; only the layout assertion fails. This is the concrete shape of `CLAUDE.md`'s own warning that "the current automated checker is narrower than the complete V3 page contract". Either the contract or the vault is wrong, and that is an owner decision. |

## 4 · Open — design

| # | Issue | Detail |
|---|---|---|
| ⏳ 29 | **Vault is one repo; v2 designed per-department** | `Proposal_SNP_Memory_System_v2.md:121` specifies `Git vault (per-department)`. Built: one repo, 432 pages, ACLs in frontmatter enforced only by RLS at query time. **RLS gates the index; git gates nothing.** Pluralism requires each person to hold a clone — and a clone is an all-departments clone. Blocks the IT rollout, not Monday. Recorded in `docs/VAULT_ACCESS_AND_PLURALISM.md`. |
| ⏳ 30 | **Lock service never built** | Specified in v0.1 §6 / v0.2. v0.1 is explicit that human-vs-agent collisions cannot be solved by locking and CRDT merges produce coherent-looking nonsense. Harmless with one human. |
| ⏳ 31 | **TLS deferred** | Emitted config uses `http://` + `--allow-http`; bearer token crosses the wire in clear. Acceptable on a trusted LAN; blocks anything wider. |
| ⏳ 32 | **`basic-memory/` dead but load-bearing** | Nothing builds it, but `requirements.lock` is a tracked release-manifest input (`write_release_manifest.py:26`). |
| ⏳ 33 | **E11 — Obsidian hop** | Owner chose Option A (owner-only clone, manual git first). Gate remains open pending demonstration. |
| 🚫 34 | **Department RLS buys less** | Consequence of choosing per-team stacks over one shared instance. Accepted, recorded. |
| 🚫 35 | **Connect path is scout-only** | `snpmemory` stdio needs a checkout by construction. Correct: reading needs no local state, authoring needs a checkout anyway. |

## 5 · Measurement findings

| # | Finding | Detail |
|---|---|---|
| ❓ 36 | **I-3 fails at the median, not the tail** | p50 **5.653 s**, p95 7.781 s against a 5 s bar. Bar since rewritten to 10 s p95, n=10, nearest-rank. |
| ❓ 37 | **The dominant latency span is unexplained** | `push → snapshot publication` p95 **3.900 s** ≈ 50% of budget. Embedding is only 0.974 s. Measured directly: `git archive` + `tar extract` of all 433 files takes **0.109 s** — so the obvious "re-materialises everything" hypothesis accounts for ~3%. Cause still unknown; webhook dispatch and fetch not yet separated. |
| ⏳ 38 | **86% of the vault has no `## TL;DR`** | 62/433 → since 59 exact-match. TL;DR is chunk 0 — the first text a query scores against. 371 pages retrieved on arbitrary body text. |
| ⏳ 39 | **Vietnamese recall is the weak arm** | en `recall@1 0.95` vs vi **0.70**; 276 pages contain Vietnamese. |
| ⏳ 40 | **`.unlazy/` is gitignored** | The evidence ledger whose value is being unforgeable lives outside version control. Destroyed once by an over-greedy `re.DOTALL`; recovered only because a gate count looked wrong. |
| ⏳ 41 | **Reporting mismatch** | Three consecutive reports said "0 abandoned" against a ledger reading 1. Now partly reconciled. |

## 6 · Process failures worth naming

| # | Failure | Lesson |
|---|---|---|
| 42 | **The response-shape change broke consumers three times** | Each sweep declared itself complete; the next found more. Grep for the *function* misses MCP-**client** callers entirely. |
| 43 | **Four agents stepped around `full-chain`** | Each labelled it "pre-existing, not mine" — including me, twice. A defect with a label is a defect nobody owns. |
| 44 | **Two confident bottleneck predictions, both wrong** | Embedding (actually 0.974 s) and snapshot materialisation (actually 0.109 s). This stack does not behave the way its shape suggests. Measure. |
| 45b | **Three defects, one pattern: tests asserting shape instead of behaviour** | #52 the sparse arm returned nothing for 35/40 queries while every test passed; #59 all four `verify_vault` tests monkeypatch the very function holding the bug; #65 a test *asserts* the duplicate-tool-name configuration is correct. In each case the suite checked the structure a thing declares, never the result it produces. The three were found by measuring output, by control-testing with a deliberately wrong input, and by a user asking "where does it actually read from" — none by a test. |
| 45 | **A gate that never failed proved nothing** | The eval control, the description-overlap guard and the schema check each had to be *watched failing* before being trusted. Two were vacuous until forced. |

## 7 · My own errors in this review

Recorded because they changed what was planned.

| # | Error | Correction |
|---|---|---|
| 46 | Claimed **"the authoring half is untested"** | False. **33 tests** across `test_propose_page.py`, `test_cli_authoring.py`, `test_plan_articles.py`, including `test_propose_never_commits_to_the_branch_you_are_on`. I grepped hyphenated CLI names against files importing Python functions. |
| 47 | Claimed **"the history has never been examined"** | False. `scripts/scan_secrets.py` scans working bytes, staged blobs, untracked files and all-ref history, and ships as `snpmemory verify-secrets`. An entire planned workstream evaporated. |
| 48 | Invented an **"option E — restore the courier"** from V1 | The source says the opposite: humans commit themselves, agents only open PRs. I built it on recollection without reading `Proposal_LLM_Wiki_v0.2.md`. |
| 49 | Wrote a plan with an **unreachable `total_count`** and four nonexistent test fixtures | Caught in pre-flight, before dispatch. |
| 50 | My stale-shape sweep **missed a bare `[]`** | Grepped prose patterns; the offender was a JSON block. Reported "zero survivors" wrongly. |
| 51 | Wrote "**two** deliberate departures" above three bullets | Propagated into a commit message before review caught it. |

---

## What is actually urgent

1. **27 + 28 — OpenCode packaging.** Two known causes, neither fixed. P-1 fails on the scorecard.
2. **37 — the unexplained 3.9 s.** Half the latency budget with no attributed cause.
3. **29 — one repo, no per-department git boundary.** Blocks handing a clone to anyone else.

Everything else is either fixed, recorded as a decision, or genuinely not urgent.
