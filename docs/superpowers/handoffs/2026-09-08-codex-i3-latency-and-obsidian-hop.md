# Handoff — I-3: why it fails, and what to do about it

**To:** Codex
**From:** Claude (engine lane), 2026-09-08
**Status of the tree:** `origin/main` and `gitea/main` both current. GitHub main
`3cc7ad8` (system only, `wiki/` = 12). Gitea main `8c28208` (system + the 433-page
vault). All six containers healthy, images stamped at `3cc7ad8`,
`snpmemory status` = ok / exit 0.

---

## The problem in one paragraph

The demo scorecard's I-3 reads *"Human edit in Obsidian updates index in < 5s."*
It fails for **two unrelated reasons**, and they need separating before anyone
tries to "fix the latency".

1. **Scope.** I-3 starts its clock in Obsidian. The Obsidian→Gitea hop does not
   exist — the vault at `~/Documents/memo-project/Obsidian Vault` is not a git
   checkout. This is gate **E11**, deliberately abandoned on 2026-09-07 (see
   `.unlazy/v3-retrieval/gates/engine-2026-09-04.md`, the `ABANDON:` line). No
   pipeline tuning satisfies I-3 while its first hop is unbuilt.
2. **Latency.** The span that *does* exist — `git push` → queryable in the served
   index — measures **~7 s** against I-3's 5 s bar. Measured by
   `engine_acceptance.py --group vault-change-propagates`.

The runbook now says I-3 is expected to fail and tells the presenter to record
the observed time rather than adjust the criterion
(`docs/DEMO_OPENCODE.md:416`). The criterion itself was deliberately **not**
relaxed — moving the bar to match the measurement would be manufacturing a pass.

---

## Task 1 — find where the ~7 seconds goes

### Why it is not already known

`n = 1`. There is one measurement, from one gate run. Nobody has a per-stage
breakdown, and **sync-job emits no per-stage log lines** — grepping its logs for
`indexed|ingest|batch|embed|chunk` returns nothing. host-sync *does* log
timestamped `Published wiki snapshot at commit <sha>`, so the first half is
readable from logs and the second half is dark.

### The chain

```
git push  →  Gitea webhook  →  host-sync fetch + snapshot + retarget `current`
          →  sync-job watcher notices  →  chunk  →  embed via LiteLLM
          →  Postgres write  →  queryable through wiki_search
                                   ▲
                     everything from here left no trace
```

### Do this, in order

**1. Passive first — zero risk to the rehearsal.** Reconstruct
`push → snapshot published` from existing host-sync logs:

```bash
docker compose logs host-sync --timestamps --no-log-prefix | grep 'Published wiki snapshot'
```

Pair those against the probe commits already in the vault repo's history
(12 `V3-PROP-*` commits from 2026-09-07 runs). Subtracting gives you the
host-sync half. Whatever remains of the ~7 s is downstream, and that alone tells
you which half to attack.

**2. Then instrument sync-job.** This is the real deliverable — the missing
observability is why the number is unexplained. Emit one structured line per
stage with a correlation id (the commit sha works), covering: watcher wake,
chunk complete, embed request sent, embed response received, Postgres commit,
row visible. Follow the repo's existing logging idiom in `scout/sync_job.py`.

**3. Only then measure with an active probe.** Note it writes a page into the
vault and forces a re-index — the same corpus OpenCode reads. If it dies midway
a `V3-PROP-<uuid>.md` page lingers in the index and an agent can retrieve it
live. **Do not run this during or immediately before the rehearsal.**

**4. Report p50/p95 over N≥10, not one sample.** A single 7 s observation is not
a distribution, and an acceptance bar judged against `n=1` is not measurable.
This is the practice that was missing.

### Prime suspects, in rough order

- **Embedding round-trip to LiteLLM.** Network + provider latency on a
  single-file change. Check whether a one-page edit still submits a full-size
  batch — batch sizing tuned for bulk ingest is wrong for a one-page edit.
- **Watcher debounce / poll interval** in `scout/sync_job.py`. If it waits for a
  quiet period before acting, that interval is a floor on the whole budget.
- **Snapshot materialisation.** host-sync extracts a snapshot per commit. Verify
  it is not re-materialising all 433 pages for a one-file change.
- **Re-embedding unchanged content.** A content-hash short-circuit already exists
  (`content_hash` + `model` + `chunk_policy` + `capability_fingerprint`).
  Confirm it is actually hitting on a one-page edit rather than silently missing.

Do not guess and tune. Measure, then fix the stage that dominates.

---

## Task 2 — the Obsidian→git hop (E11)

This is the owner's decision, not an engineering one — it changes how he works
day to day. Present options; do not pick for him.

The three recorded options (full commands in
`docs/superpowers/plans/2026-09-05-lane-claude-engine-and-infra.md` Task 9):

- **A — sparse checkout.** Clone the vault repo, `sparse-checkout` only `wiki/`,
  point Obsidian at the clone. He commits and pushes.
- **B — a sync script he runs.** Makes W-2's *"no operator action required"*
  wording false, which matters because that phrase is load-bearing in the demo.
- **C — demo from a checkout that is not his daily vault**, narrated as such.

**Best practice worth adding as a fourth option:** the **Obsidian Git plugin**
on top of option A. It is the well-trodden solution for exactly this, keeps
Obsidian as the editor, and auto-commits on an interval — which is what makes
"zero operator commands" literally true rather than a fudge.

**The catch, and it is decisive for I-3:** that auto-commit interval becomes part
of the latency budget. A plugin committing every 60 s makes any sub-5-second
end-to-end claim impossible by construction. If the owner picks it, I-3 must be
re-scoped to the push→index span regardless of how fast the pipeline gets.

---

## Task 3 — rewrite I-3 so it is measurable

Current wording is unmeasurable in three ways: the start point is ambiguous
(editor? push?), there is no sample size, and there is no condition (cold cache?
one page or fifty?).

Do not touch it until Task 1 has a distribution. Then propose something shaped
like:

> **I-3: Edit propagates.** A commit pushed to the vault repo's `main` is
> returnable by `wiki_search` within **N seconds at p95 over 10 consecutive
> single-page edits**, with the watcher running and the index warm.

Pick `N` from the measured p95 with headroom, and state plainly whether the
Obsidian→push hop is in scope or out. If the owner adopts an auto-commit plugin,
add its interval as a separately stated number rather than burying it in the
total.

**Take the new number to the owner before shipping it.** The reason the old one
was left alone is that quietly widening a bar to match a measurement is how a
system starts lying about itself.

---

## Ground rules that still apply

- Reach vault **content** only through the served MCP surface — never the
  filesystem, never PostgreSQL directly. Operational metadata is fine.
- Feature branch + reviewed PR (R-6.4 / R-7.3). Both mains are current; do not
  push to them directly.
- Never commit these six: `.obsidian/app.json`,
  `artifacts/superpowers/execution.md`, `artifacts/superpowers/plan.md`,
  `Untitled.md`, `docs/agy/`, `docs/image-conv-with-boss/` — the last one is
  screenshots of the owner's conversation with his boss and `origin` is a public
  repo.
- Do not edit `artifacts/audits/**`, `docs/proposal/**`,
  `docs/SKILLS_AUDIT_REPORT.md`, `docs/conversation.md`, `.unlazy/**`.
- Rebuild images **last**, after the final commit — every commit re-stales the
  revision stamp by design:
  `SNP_GIT_REVISION=$(git rev-parse HEAD) docker compose build scout sync-job host-sync`
- Baselines: offline suite **1375 passed / 29 deselected**; ruff, ruff-format and
  `mypy scout scripts` clean.
- `e2e_retrieval.py --group full-chain` fails on a pre-existing
  `_IngestConnection.fetch` AttributeError. Not yours, predates this work.

## Also still open, unrelated to I-3

**OpenCode client packaging.** The clean-install probe found `mcp: null` and zero
SNP skills; `SUPPORTED_CLIENTS` is `['cursor','vscode','claude','gemini']` with no
`opencode` target, and grepping `opencode` across `scout/` and `scripts/` returns
nothing. `docs/DEMO_OPENCODE.md` now documents the gap honestly instead of
printing a command that does not exist. Until it is closed, the rehearsal is
Acts 1–2 only, read-only, against the blank scorecard.

---

## Task 4 — add three missing sections to the project `CLAUDE.md`

Land this in the **same PR** as the work above so `main` goes out of date once,
not twice.

The existing `CLAUDE.md` is deliberate and correct — it encodes the retrieval
contract and points at `AGENTS.md`. **Do not rewrite it.** Add to it. Every
command below was run during the 2026-09-08 session; none is invented.

### 4a. The two-remote trap — add this first, it is the dangerous one

Nothing currently warns a reader that the two remotes hold different vault
content. A push to the wrong one publishes the lead's entire vault to a public
repository, and git history keeps it after deletion.

```markdown
## Remotes — the two are not interchangeable

    origin (GitHub, PUBLIC)          gitea (private)
    wiki/ =  12 sample pages         wiki/ = 433 real pages
             ▲                                ▲
             └── a wrong push here publishes ─┘
                 the lead's entire vault

`origin` carries the **system only**. The lead's 433-page vault lives on `gitea`
and must never reach GitHub. Before any push that could touch `wiki/`, check
both sides:

    git ls-tree -r --name-only origin/feat/<branch> -- wiki/ | wc -l   # expect 12
    git diff --name-only <base>..HEAD -- wiki/ | wc -l                 # expect 0

The Gitea integration branch merges system code onto the vault repo. Verify the
vault tree is byte-identical before pushing it:

    git rev-parse <integration>:wiki
    git rev-parse gitea/main:wiki      # these must match

Never commit these six working files — the last is screenshots of the owner's
conversation with his boss, and `origin` is public:
`.obsidian/app.json`, `artifacts/superpowers/execution.md`,
`artifacts/superpowers/plan.md`, `Untitled.md`, `docs/agy/`,
`docs/image-conv-with-boss/`. Stage explicit paths; never `git add -A`.
```

### 4b. Commands

```markdown
## Commands

Offline suite — delete the generated dirs first or the package-sync test fails:

    rm -rf .agents .codex
    env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY \
      uv run pytest -m 'not integration' --disable-socket -q

One test:  `uv run pytest tests/test_host_sync.py::test_name -v`

    uv run ruff check . && uv run ruff format --check .   # line-length 88
    uv run mypy scout scripts                             # strict

The agent package is mirrored into `.agent/` and `.claude/`. Edit
`packages/snp-agent/` and sync — never hand-edit a mirror:

    python3 scripts/export_agent_bundle.py --sync
    python3 scripts/export_agent_bundle.py --verify

Live gates (need `set -a && . ./.env && set +a`):

    .venv/bin/python artifacts/v3/checks/engine_acceptance.py --group find-read-cite
    .venv/bin/python artifacts/v3/checks/mcp_eval.py --group answerable
    .venv/bin/python artifacts/v3/checks/e2e_retrieval.py --group contract-matches-surface
    uv run snpmemory status -o json        # exit 0 only when images match HEAD

Rebuild images **last**, after the final commit — every commit re-stales the
revision stamp by design, and `snpmemory status` reports `degraded` until you do:

    SNP_GIT_REVISION=$(git rev-parse HEAD) docker compose build scout sync-job host-sync
    docker compose up -d

Known-red and not yours: `e2e_retrieval.py --group full-chain` fails on a
pre-existing `_IngestConnection.fetch` AttributeError.
```

### 4c. Architecture

```markdown
## Architecture — the index finds, the vault answers

    ┌── retrieval ────────────────────────────────────────────┐
    │  wiki_search  →  candidate pages + routing snippets      │
    │       │           (a snippet is never answer text)       │
    │       ▼                                                  │
    │  wiki_read    →  the canonical page                      │
    │                  tldr → outline → section → full         │
    └──────────────────────────────────────────────────────────┘

    ┌── propagation ──────────────────────────────────────────┐
    │  push → webhook → host-sync → watcher → embed → index    │
    │                      │                          ▲       │
    │                      └── snapshot + `current` ───┘       │
    └──────────────────────────────────────────────────────────┘

`wiki_search` returns an **envelope**, not a bare list:
`{"results": [...], "returned": N, "suppressed_as_seen": N, "has_more": bool}`.
A page already named in `seen` comes back redacted to
`{path, title, seen: true}`. Both tools declare an `output_schema`, so a
client's `result.data` is a coerced pydantic model — read `structured_content`
for the plain dict. Three separate gate consumers broke on that coercion during
the 2026-09-08 session; if you change the response shape, sweep for MCP-client
callers, not just callers of the tool function.

Two MCP servers: `scout` (authenticated, Streamable HTTP) and `snpmemory`
(local, stdio). Embeddings go through LiteLLM at 1024 dimensions. There is no
`basic-memory` container and no healer — the directory survives on disk, unbuilt,
with its `requirements.lock` still a release-manifest input.

Acceptance gates live in `.unlazy/v3-retrieval/gates/`. That ledger is
gitignored, so a destructive edit to it cannot be recovered from git — copy it
before any scripted edit.
```

### Verify before committing

```bash
rm -rf .agents .codex
env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest -m 'not integration' --disable-socket -q
uv run ruff check . && uv run ruff format --check . && uv run mypy scout scripts
```

`tests/test_docs_contract.py` and `tests/test_docs_surface_currency.py` both read
shipped docs — if either fails on your `CLAUDE.md` edit, fix the cause, do not
weaken the test.
