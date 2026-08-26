## Goal

Create a phased route that closes the twelve active gaps without treating all of them as equal. The immediate objective is a reproducible, proven first production release; broader content, extraction, and source-health work follow as explicit product increments.

## Constraints

- The current worktree is held: no commit, rebuild, restart, re-ingest, or push is authorised until the owner lifts that hold.
- The live corpus was host-built and the running images predate the current code, so any broad Compose action before a clean release can corrupt or regress the corpus.
- Wiki search, RAG retrieval, and agent-local operations are intentionally separate systems; tests must prove the real hand-offs rather than a substitute engine.
- The judge has a limited daily budget; content generation must be resumable and capped.
- Source-health decisions need editorial policy, not just code: thresholds, report-vs-quarantine, Office-format scope, and where live validation runs.
- All mutations remain PR-first, with database privilege boundaries and safe rollback.

## Known context

- The twelve gaps fall into five groups: release coherence; live integration/security operations; retrieval/content quality; source-data lifecycle and extracted assets; agent usability/future protocol adoption.
- Static hardening is in the worktree, including pinned actions/images/dependencies, database audit events, and host parser goldens, but its Dockerfiles have not been built.
- The real first-hop basic-memory test, multilingual model decision, `extract` command, derived asset store, source-health lifecycle, release tag, runner exercise, and generated agent CLI reference do not exist yet.
- Native MCP Tasks were investigated and intentionally deferred because the available SDK path brings Redis/pydocket while offering weaker persistence than current on-disk task handles.

## Risks

- Building or restarting before the clean-release step can re-ingest with stale parsing behavior and damage corpus integrity.
- Treating static tests as deployment proof hides build, dependency, migration, and runtime failures.
- Choosing a multilingual model or extraction stack without a measured corpus and reindex plan can lower English quality or widen the attack surface.
- Auto-quarantine without reversible state, clear agent status, and human review can remove valid evidence.
- The runner’s Docker socket and unknown bot-token scopes remain high-impact whenever the runner is enabled.
- Trying to implement all twelve at once would mix owner policy choices, content spending, runtime changes, and security work into an unreviewable release.

## Options (2–4)

### Option 1 — Release only

**Summary:** Commit the held work, produce a clean image/corpus release, prove the actual MCP path, then pause.

**Pros / cons:** It removes the highest integrity uncertainty quickly and creates a trustworthy baseline. It leaves multilingual retrieval, corpus breadth, source health, extraction, and agent usability incomplete.

**Complexity / risk:** Moderate operational risk; low code-scope risk if the release is staged and backed up.

### Option 2 — Product breadth first

**Summary:** Compile more articles, change search to multilingual, add `extract`, and improve agent documentation before deployment cleanup.

**Pros / cons:** It makes the demo more compelling, but tests product features on a runtime that cannot reproduce current source. It also compounds model-budget and parser-security risk.

**Complexity / risk:** High; not appropriate before release coherence.

### Option 3 — Source-health/security first

**Summary:** Build quarantine, validation, sandboxing, content-hash checks, and asset governance before expanding the corpus.

**Pros / cons:** It gives the strongest long-term evidence integrity. It is the largest design project, requires four owner policy decisions, and delays useful product validation.

**Complexity / risk:** High; correct as a second major program, not the first release gate.

### Option 4 — Staged release program

**Summary:** Make release coherence the mandatory gate, then prove user-facing retrieval, then grow content, then add source-health and asset lifecycle capabilities as separately releasable epics.

**Pros / cons:** Keeps each release reviewable, exposes real runtime failures early, and maps every gap to an owner and acceptance test. It requires discipline not to start later phases early.

**Complexity / risk:** Moderate overall; lowest data-integrity and schedule risk.

## Recommendation

Choose Option 4.

1. **R0 — Clean release baseline:** lift the hold; commit reviewed work; build all three local images; apply migrations; start the stack in a controlled window; re-ingest inside the container; verify image revision, capability fingerprint, vault, addresses, RLS, and a new container parser golden. Tag this release, then make the installer default to that tag. This closes or materially validates gaps 1, 2, 4, and 11.

2. **R1 — Prove the real operational paths:** add the live basic-memory snapshot → `search_notes` → `read_note` test; start the runner only in a controlled environment; manually run `workflow_dispatch`; inspect and right-size `BOT_TOKEN`; decide whether the Docker socket is accepted or moves to rootless-host work. This closes gap 5 and validates gap 3.

3. **R2 — Retrieval and knowledge quality:** make the OD-1 language choice using a fixed probe set; if multilingual support is required, integrate a compatible model, reindex, and set regression thresholds. Approve the six-article budget, re-mint the bad hint, recompile legacy pages for Works Cited, and set a corpus-growth target. This addresses gaps 6 and 7.

4. **R3 — Evidence lifecycle and derived assets:** first implement the source-health minimum slice—persistent outcomes, chunk-presence validation, content hashes, fresh report mode, explicit `SOURCE_QUARANTINED` behavior, and reversible operator workflow. Then add parser isolation/limits and decide Office support. Separately decide `derived/` location, retention, and mandatory source-ACL inheritance, then implement and test `extract`. This closes gap 10 and gap 8 without weakening evidence controls.

5. **R4 — Adoption and future protocol work:** generate an agent-package CLI reference from `snpmemory schema`, with a small hand-written usage guide and a drift test. Keep native MCP Tasks deferred until Redis or a dependency-free durable task backend is justified; set a dated revisit trigger rather than treating it as missing functionality. This closes gap 9 and gives gap 12 a deliberate lifecycle.

## Acceptance criteria

- A tagged commit produces the exact deployed images; all local images build from pinned inputs, the stack starts healthy, migrations are ledger-consistent, and container-side ingestion produces a verified corpus without unexpected deletions.
- The deployed basic-memory MCP passes a real snapshot/search/read integration test; Scout ACL, address, and groundedness gates pass against the release corpus.
- The language contract is explicit: either multilingual retrieval meets a recorded probe threshold after reindexing, or English-only use is formally accepted and documented.
- The corpus has the approved additional articles, the known bad hint is re-minted, and legacy pages are recompiled where Works Cited coverage is required.
- `extract` exists only after derived assets demonstrably inherit source ACLs, have retention rules, and reject path/scope bypasses.
- Source health records outcomes even for zero-chunk failures; detects changed evidence; reports quarantine distinctly to operators and agents; and processes hostile files under bounded time, memory, expansion, and concurrency limits.
- The runner has a recorded token-scope decision, a successful manual dispatch record, and an explicit socket-host trust decision.
- The installer defaults to a release tag, and the agent package contains a generated, CI-checked CLI reference.
- Native MCP Tasks either have a justified, tested implementation or retain a dated, evidence-backed deferral decision.
