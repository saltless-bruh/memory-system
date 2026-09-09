# Install Simplification — Design

**Date:** 2026-09-08
**Status:** design approved in conversation; implementation deferred until after
rehearsal 1 (the lead demo) succeeds.
**Audience today:** the owner's team and lead. **Audience next:** other IT
departments, each running their own stack.

---

## Context

Installing the system today takes eight commands, four of which fail in ways the
person running them cannot diagnose:

```
git clone … && cd memory-system
pip install -e .
snpmemory init
$EDITOR .env                  ✗ which keys? which URL? nothing validates it
snpmemory up                  ✗ six containers; failure = a wall of compose output
snpmemory ingest-wiki         ✗ silent if the vault URL was wrong
snpmemory mcp-config --client claude
snpmemory install-agent .
```

Two defects make this worse than the step count suggests.

**`install-agent` writes no entry file.** It creates `<target>/.agent` and stops.
Nothing tells the agent to read it — no `CLAUDE.md`, no `AGENTS.md`, no
`GEMINI.md`. This is a second, independent reason Codex's clean-install probe
found zero SNP skills; the missing MCP config was only the first.

**Every port binds to `127.0.0.1`.** No second machine can reach any service. A
two-machine install cannot be tested at all until that changes.

### The decision that shapes everything

Each team runs its own stack — not one shared instance. But within a team, **most
people are connectors, not hosters.** One person boots the stack; teammates only
need their agent wired to it. One installer was the wrong shape, because hosting
and connecting have different prerequisites and different audiences.

---

## Non-goals

- Publishing to PyPI, a `--demo` mode with a sample corpus, or `curl | bash` for
  the full stack. Those serve strangers; strangers are not the audience.
- Shipping the vault. Each install brings its own knowledge. The system is the
  machine that indexes a vault, not the vault.
- Reworking department RLS. Per-team stacks make it buy less than it could, which
  is recorded as a known consequence, not addressed here.

---

## The two flows

```
┌─ CONNECT — most people, any OS ───────────────────────────────┐
│  npx snp-memory init                                           │
│     ▸ picker: Claude Code · OpenCode · Codex · Gemini          │
│       (pre-selected from detected markers; confirm or change)  │
│     ▸ asks: scout URL + token                                  │
│     ▸ writes the entry file, skills, MCP config                │
│  → no clone · no Docker · no Python · ~20 seconds              │
└────────────────────────────────────────────────────────────────┘

┌─ HOST — one person per team ──────────────────────────────────┐
│  git clone … && cd memory-system                               │
│  snpmemory bootstrap                                           │
│     ▸ doctor → prompts → up (5 containers) → ingest            │
│     ▸ prints the scout URL and token to hand teammates         │
└────────────────────────────────────────────────────────────────┘
```

The hoster's final output is exactly the two values connectors need. That is the
seam between the flows, and the only thing that crosses it.

### Why `npx` for connect

Node is already a hard requirement: the emitted MCP config runs
`npx -y mcp-remote` to reach scout. So `npx` adds **zero new prerequisites**,
while `uv`, `pipx` or a Python toolchain each add one — and that cost lands
hardest on Windows, where most of the future audience is.

| Channel | Windows | Mac | New prerequisite | Maintenance |
|---|---|---|---|---|
| **npx** | ✓✓ | ✓✓ | **none** | low |
| uv tool | ~ | ✓ | uv | low |
| pipx / pip | ~ | ✓ | python + pipx | low |
| curl │ iex | ✓ | ✓ | none | two scripts to keep in step |
| native binary | ✓✓ | ✓✓ | none | per-platform release CI |

Windows and Mac friction therefore lands only on the one person per team who
hosts — and they can host on a Linux box while working from either.

---

## Agent matrix

The shipped content is identical for every agent. Only three things vary.

| Agent | Entry file | Skills directory | MCP config |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` | `.mcp.json` |
| OpenCode | `AGENTS.md` | `.opencode/skills/` | `opencode.json` |
| Codex | `AGENTS.md` | `.agent/skills/` | per its own config |
| Gemini (Agy) | `GEMINI.md` | `.gemini/` | per its own config |

**Entry files are thin pointers**, mirroring what this repo already does —
`AGENTS.md` is canonical and `CLAUDE.md` delegates to it. One source of truth,
three filenames.

**Writes are non-destructive.** If an entry file already exists, append a
delimited SNP block rather than overwrite. Somebody's existing instructions must
survive an install.

**Detect, then let the user pick.** Markers (`.claude/`, `.opencode/`,
`GEMINI.md`) pre-select the picker; the user confirms or changes. With four
agents this is strictly better than spec-kit's pick-only model, and never
silently wrong. Keep spec-kit's escape hatches for the same reasons they exist:
`--agent <name>` to skip the picker, and `--non-interactive` so a harness without
a PTY never hangs. Add `snp-memory agents list`, `--here` for existing
directories, and `--force`.

---

## What connectors get, and why that is correct

```
scout      wiki_search · wiki_read                 ← reading
snpmemory  wiki_search · wiki_read  (same two)
           + verify · plan_articles
             compile_plan · compile_status         ← authoring
```

`snpmemory` duplicates both retrieval tools, so a connector loses **no reading
capability**. What it loses is authoring — and authoring already requires a
checkout independently of MCP, because no exposed tool performs a git push
(`AGENTS.md`, rules R-6.4 / R-7.3). Writing a page means clone → edit → commit →
push → PR; anyone doing that has the repo, and can have the CLI.

So the split follows a real boundary: **reading needs no local state; changing
knowledge needs a checkout by definition.**

Watch, do not pre-build: `compile_status` is read-only and may eventually be
wanted remotely. If that demand appears, migrate that one tool to scout.

---

## Phasing

Two implementation plans, not one. Phase 1 is entirely in-repo Python and YAML
against machinery that already exists. Phase 2 is a different language, a
different release process, and the only part with no existing foundation.

```
  PHASE 1 — in-repo, testable on its own
     1  git behind a profile          6 → 5 containers
     2  snpmemory doctor              extend collect_findings
     3  snpmemory bootstrap           host path: 8 commands → 3
     4  agent-aware install-agent     CLAUDE.md │ AGENTS.md │ GEMINI.md
     5  SCOUT_BIND                    two-machine testing becomes possible

  PHASE 2 — new machinery, deferred
     6  npx snp-memory init           removes the clone from the connect path
        + versioned skills bundle as a GitHub release asset
```

**What phase 1 delivers standalone.** The host path drops from eight commands to
three. The entry-file bug is fixed, which is one of the two root causes behind a
clean OpenCode install seeing zero skills. And rehearsal 2 becomes runnable —
the laptop still needs a clone to connect, but the two-machine path works
end to end. Phase 2 removes that last clone; it does not unlock the test.

**Why phase 2 waits.** Publishing a versioned skills bundle and an npm package
that fetches it is the largest genuinely new thing in this design, and none of
phase 1 depends on it. Shipping phase 1 first means the agent matrix and the
bootstrap are already proven by the time the distribution channel is built,
so a phase 2 failure is isolated to distribution rather than tangled with
install correctness.

---

## Component changes

Implement in this order. Each stage changes what the next has to orchestrate.

### 1. Make `git` optional (was "approach B")

`GIT_SYNC_URL` is already just a URL — `.env.example` ships
`https://git.example.invalid/…`. Only `host-sync` and `gitea-runner` depend on
the `git` service, and the runner is already behind a profile. Teams whose vault
lives in GitHub Enterprise or GitLab do not need a bundled Gitea.

```yaml
git:
  profiles: [bundled-git]     # off by default → 5 containers
```

**This walks back part of the 2026-09-08 `depends_on` fix, deliberately.**
`host-sync` currently declares `depends_on: git: service_healthy`, which would
make it refuse to start for every team using an external remote. The retry added
in the same session (`GIT_SYNC_ATTEMPTS`, `GIT_SYNC_RETRY_SECONDS`) already
absorbs the startup race that `depends_on` was added for — belt and braces, and
only the braces are needed once `git` is optional. Move the `depends_on` into a
`docker-compose.bundled-git.yml` override applied with the profile, so it is
correct in both shapes and no team depends on a service it does not run.

Keep `litellm`. It pins `dimensions: 1024` and is what prevents the two-vector-
space class of bug this system already hit once.

### 2. `snpmemory doctor` (was "approach C")

Mostly built already. `scripts/preflight_stack.py` has `collect_findings()` and
`exit_code_for()`, is wired into `snpmemory status`, and carries 15 tests. Extend
that findings list with install-time checks rather than starting new machinery,
so `status` and `doctor` stay one mechanism:

- Docker present and running
- Ports 8080 / 9000 / 5432 / 4000 free, naming the holder when not
- Python 3.12
- Embedding key reachable **and returning dimension 1024**
- Vault URL reachable
- Disk space

Exit non-zero with a numbered list of what to fix.

### 3. `snpmemory bootstrap` (was "approach A")

```
doctor → prompt (embedding key, vault URL) → write .env + .secrets/
       → up → wait healthy → ingest → print scout URL + token
```

Three properties matter more than the step list:

- **Idempotent.** Re-running skips completed work. Installs get interrupted.
- **Non-interactive mode.** `--non-interactive` reading environment variables, so
  this works in CI and in scripted rollouts to other departments later.
- **Never half-state.** Stop at the first failure with a doctor-style message
  rather than leaving four containers up and no configuration.

### 4. Agent-aware `install-agent` — **phase 1**

Today `install-agent` copies into `<target>/.agent` and writes no entry file, so
nothing tells the agent the package exists. Make it write, per the agent matrix
above: the entry file, the skills directory, and the MCP config.

This is the half of the connect story that needs no new distribution channel,
and it is in-repo Python and shell. It fixes a live defect rather than adding a
feature, which is why it belongs in phase 1 rather than waiting on npm.

The picker, `--agent`, `--non-interactive`, `--here`, `--force` and
`agents list` all land here. Phase 2 reuses this code path rather than
reimplementing it in JavaScript — the npm package is a delivery mechanism for
this logic, not a second copy of it.

### 5. Network binding

Only scout becomes configurable, defaulting to loopback so single-machine
installs are unchanged:

```yaml
scout:
  ports: ["${SCOUT_BIND:-127.0.0.1}:${SCOUT_PORT:-8080}:8080"]
```

Postgres, LiteLLM, Gitea and host-sync stay on `127.0.0.1`. Nothing on a
connector's machine needs them.

**Recorded security decision.** The emitted config uses `http://` with
`--allow-http`, so the bearer token crosses the wire in clear. Acceptable on a
trusted LAN for rehearsal 2. **TLS is required before this leaves a trusted
network**, and that is a blocker on any wider rollout, not a nice-to-have.

---

## Testing

The install path is currently the least-tested thing in the repository.

- `bootstrap --dry-run` against a scratch directory, asserting the file writes
  and their ordering. Extend `tests/test_preflight_stack.py` rather than starting
  a new suite.
- A matrix test over the four agents asserting the correct entry file, skills
  directory and MCP config for each, and that an existing entry file is appended
  to rather than overwritten.
- A `doctor` test per check, each observed failing against a deliberately broken
  condition. A check never seen failing is not evidence it works.

### Rehearsal 2 is the acceptance test

Two machines: the PC hosts, the laptop connects. It tests what rehearsal 1
structurally cannot — **the install, on a machine that has never seen this
repository** — and it is the only way to learn what a Windows or Mac connector
actually experiences.

Sequencing: rehearsal 1 (needs nothing new) → build the above → rehearsal 2 as
the acceptance test. Codex hosts; OpenCode and Claude Code connect, which
exercises two agents, two different entry files and one shared stack. That
matrix would have caught both OpenCode defects.

---

## Risks and open questions

- **npm release plumbing is new.** The skills bundle must be versioned and
  published, and the npm package kept in step with it. This is the largest piece
  of genuinely new machinery in the design.
- **`--agent all`** for shared repositories where teammates use different tools:
  worth having, but decide whether four entry files in one repository is helpful
  or noisy before building it.
- **Windows path handling** in the file writer is untested territory for this
  codebase; the two-machine rehearsal is the first real exercise of it.
- **TLS** is deferred, not solved. It gates any use beyond a trusted network.

---

## Phase 2 — `npx snp-memory init` (deferred)

Not part of the first implementation plan. Recorded here so the phase 1 work
does not paint it into a corner.

A thin Node CLI whose only jobs are: run the picker, ask for the scout URL and
token, download the versioned skills bundle from a GitHub release, and write the
agent-specific files. It must not require a checkout, Docker, or Python.

**It is a delivery mechanism for phase 1's logic, not a reimplementation.** The
agent matrix, the non-destructive append, and the config shapes are settled in
phase 1 and consumed here. If phase 2 finds itself re-deciding any of those,
that is a signal phase 1 left something underspecified.

New machinery this repository does not have:

- A versioned skills bundle published as a GitHub release asset.
- An npm package that fetches it, pinned to a bundle version.
- Release plumbing to keep the two in step, so `npx snp-memory@x.y.z` and the
  bundle it downloads cannot drift apart.

**Constraint carried from phase 1:** the connect path delivers `scout` only.
The `snpmemory` stdio server needs a checkout by construction, so it cannot be
part of an npx install.

**Open question to settle before building:** whether the bundle is fetched at
install time (always current, needs network, can break offline) or vendored into
the npm package (offline-capable, but a stale package ships stale skills).
Fetching is the better default; the decision needs recording either way.
