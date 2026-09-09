# Installer CLI — Blueprint

**Date:** 2026-09-08
**Companion to:** `2026-09-08-install-simplification-design.md`
**Status:** blueprint for review. Nothing built until rehearsal 1 passes.

---

## The decision this changes

The design doc recommended `npx snp-memory init` on the grounds that Node is
already required by `mcp-remote`, so it added no prerequisite. **A Go binary is
strictly better and supersedes that.**

Node is required by the *MCP client at runtime*. It does not follow that the
*installer* should need it — and on Windows, "you already have Node" is an
assumption, not a fact. A static binary needs nothing at all.

| | npx | **Go binary** | Rust binary |
|---|---|---|---|
| User prerequisite | Node | **none** | none |
| Windows | good | **excellent** | excellent |
| TUI maturity | ink (fine) | **Bubble Tea / huh** | ratatui (lower level) |
| Cross-compile | n/a | **one command per target** | needs cross toolchains |
| New to this repo | yes | yes | yes |

**Go, with Bubble Tea.** Charm's stack — `huh` for forms, `bubbles` for spinner
and progress, `lipgloss` for style — is the most mature fit for the exact shape
here: a picker, a short form, a progress list. Rust with `ratatui` would work but
has no `huh` equivalent, so it is materially more code for the same screens.

---

## The architecture that avoids two implementations

The obvious trap is rewriting install logic in Go and letting it drift from the
Python that already does it. Avoid that with a hard rule:

> **Go renders. Python decides. JSON is the contract.**

```
        ┌──────────────────────────────────────────────┐
        │  snp   (Go · Bubble Tea)                      │
        │  ── picker · forms · progress · errors        │
        └───────────────┬──────────────────────────────┘
                        │ JSON over stdout
        ┌───────────────▼──────────────────────────────┐
        │  snpmemory  (Python · existing)               │
        │  ── doctor · bootstrap · ingest · mcp-config  │
        │     install-agent · the agent matrix          │
        └──────────────────────────────────────────────┘

  CONNECT-ONLY MODE (no Python present):
        snp writes files directly from an embedded/fetched
        bundle. This is the ONLY logic Go owns outright.
```

The repo already sets this precedent — `snpmemory status -o json` exists and is
consumed programmatically. Phase 1 extends `-o json` to `doctor` and `bootstrap`;
phase 2's Go binary renders that output rather than re-deriving it.

**What Go owns outright:** the connect path's file writing, because a connector
has no Python. That logic is small — pick an agent, write three files — and its
shapes are settled in phase 1 so Go consumes decisions rather than making them.

---

## Two binaries, two audiences

| | `snpmemory` (Python) | `snp` (Go) |
|---|---|---|
| Audience | hosts, authors | everyone |
| Surface | 26 commands, deep | 5 commands, shallow |
| Needs | checkout, Python | nothing |
| Job | operate the system | install and connect |

Precedent for the split: `rustup` versus `cargo`. One gets you set up; the other
is what you use afterwards.

---

## CLI surface

```
snp init [dir]        pick an agent, connect to a stack, write the files
snp doctor            preflight; renders `snpmemory doctor --json` when present,
                      falls back to its own network/port checks when not
snp agents            list supported agents and what each install writes
snp host              drive the host bootstrap (renders snpmemory bootstrap)
snp version           binary version + bundle version
```

Flags on `init`, mirroring spec-kit for the reasons those flags exist:

```
--agent claude|opencode|codex|gemini|all   skip the picker
--scout-url URL                            skip the prompt
--token TOKEN                              skip the prompt (or SNP_TOKEN)
--here                                     install into a non-empty directory
--force                                    overwrite instead of appending
--non-interactive                          never prompt; fail if inputs missing
--dry-run                                  print the plan, write nothing
```

`--non-interactive` is not optional polish. A harness without a PTY hangs forever
on a picker, and agent harnesses are a first-class caller here.

---

## The screens

### 1 · Detect and confirm

```
 ┌ SNP Memory · install ──────────────────────────────────────┐
 │                                                            │
 │  Directory   ~/work/acme-api                               │
 │  Detected    .claude/  →  Claude Code                      │
 │                                                            │
 │  Which agent are you setting up?                           │
 │                                                            │
 │    ▸ ● Claude Code      CLAUDE.md   .claude/skills/        │
 │      ○ OpenCode         AGENTS.md   .opencode/skills/      │
 │      ○ Codex            AGENTS.md   .agent/skills/         │
 │      ○ Gemini           GEMINI.md   .gemini/               │
 │      ○ All of them                                         │
 │                                                            │
 │  ↑↓ move · enter select · q quit                           │
 └────────────────────────────────────────────────────────────┘
```

Detection pre-selects; the user still confirms. Never silently right or wrong.

### 2 · Connection

```
 ┌ SNP Memory · connect ──────────────────────────────────────┐
 │                                                            │
 │  Scout URL   http://192.168.1.40:8080/mcp                  │
 │  Token       ••••••••••••••••••••••••                      │
 │                                                            │
 │  ⠋ checking…                                               │
 │    reachable ✓   auth ✓   departments: ai_eng, infra       │
 │                                                            │
 └────────────────────────────────────────────────────────────┘
```

Validate before writing anything. A config that points at an unreachable stack is
worse than no config, because the failure surfaces later inside an agent.

### 3 · Show the work before doing it

This screen is the whole point of a TUI here — *see what you are working with*
before it touches your project.

```
 ┌ SNP Memory · plan ─────────────────────────────────────────┐
 │                                                            │
 │  WILL CREATE                                               │
 │    .claude/skills/          7 skills                       │
 │    .mcp.json                scout (1 server)               │
 │                                                            │
 │  WILL APPEND                                               │
 │    CLAUDE.md                +12 lines in a marked block    │
 │                             your existing content is kept  │
 │                                                            │
 │  WILL NOT TOUCH                                            │
 │    .git/  src/  everything else                            │
 │                                                            │
 │  Proceed?  [y/N]                                           │
 └────────────────────────────────────────────────────────────┘
```

### 4 · Progress, then a real next step

```
 ┌ SNP Memory · done ─────────────────────────────────────────┐
 │  ✓ skills          7 written                               │
 │  ✓ CLAUDE.md       appended                                │
 │  ✓ .mcp.json       scout configured                        │
 │                                                            │
 │  Try it:  ask your agent "what does the vault say about    │
 │           SS7 interception?"                               │
 │                                                            │
 │  Not working?  snp doctor                                  │
 └────────────────────────────────────────────────────────────┘
```

### 5 · Host mode renders Python

```
 ┌ SNP Memory · host ─────────────────────────────────────────┐
 │  ✓ preflight      docker 27.3.1 · ports free · py 3.12     │
 │  ✓ secrets        written to .secrets/                     │
 │  ⠹ starting       postgres ✓  litellm ✓  scout ⠋  …        │
 │    indexing       —                                        │
 │                                                            │
 │  Hand teammates:                                           │
 │    snp init --scout-url http://192.168.1.40:8080/mcp \     │
 │             --token <shown when ready>                     │
 └────────────────────────────────────────────────────────────┘
```

Every line here is `snpmemory bootstrap --json` being rendered. Go adds no
decisions — if the TUI and the CLI ever disagree, the CLI is right.

---

## Phasing, revised

```
  PHASE 1 — Python, in-repo, ships alone
    1  git behind a profile
    2  snpmemory doctor            + --json
    3  snpmemory bootstrap         + --json
    4  agent-aware install-agent   the agent matrix lands HERE
    5  SCOUT_BIND
    → host path 8 commands → 3 · entry-file bug fixed · rehearsal 2 runnable

  PHASE 2 — Go, new machinery
    6  snp binary                  init · doctor · agents · host · version
    7  skills bundle               versioned release asset
    8  release CI                  6 targets, checksums, install script
    → connect path needs no clone, no Python, no Node
```

Phase 1 is unchanged by this blueprint except for adding `--json` to two
commands. **The language choice does not touch phase 1** — which is the point of
the render/decide split.

---

## Distribution

```
  install.sh  (mac/linux)     irm | iex  (windows)
        │                            │
        └────────► GitHub release ◄──┘
                   snp_<os>_<arch>          6 artifacts
                   skills-bundle-vX.tgz     + SHA256SUMS
```

Targets: `darwin/arm64`, `darwin/amd64`, `linux/amd64`, `linux/arm64`,
`windows/amd64`, `windows/arm64`. Go cross-compiles all six from one machine with
`GOOS`/`GOARCH`; no per-platform CI runners needed.

The repo already has release machinery to extend — `write_release_manifest.py`,
`release_preflight.py`, and tag `v0.2.1`. Note both CI workflows are currently
disabled, so release automation is a prerequisite to be re-enabled deliberately,
not assumed.

**Bundle-version pinning.** `snp vX.Y.Z` fetches `skills-bundle-vX.Y.Z`. The
binary refuses a mismatched bundle rather than installing skills it was not built
against.

**Open decision, unchanged from the design doc:** fetch the bundle at install
time (always current, needs network) or embed it in the binary (offline, can ship
stale). Embedding is more attractive with Go than it was with npm — `go:embed`
makes it trivial and removes a network dependency from the install path. My
recommendation flips to **embed**, with `snp init --bundle <path>` as the escape
hatch for air-gapped installs.

---

## Testing

- **Go unit tests** for the file writer: the four-agent matrix, append-not-
  overwrite against an existing entry file, `--dry-run` writing nothing.
- **Golden-file tests** for each agent's emitted config.
- **A cross-platform CI matrix** — the Windows path handling is untested
  territory for this project and is exactly where a file writer breaks.
- **Contract test between the two binaries:** `snpmemory doctor --json` output
  must satisfy the schema `snp` parses. Run it in the Python suite so a Python
  change that breaks the TUI fails in the repo that caused it, not downstream.

Rehearsal 2 remains the acceptance test. Phase 1 makes it runnable with a clone
on the laptop; phase 2 removes the clone.

---

## Risks

- **A second language in a Python repo.** Real ongoing cost: another toolchain,
  another CI path, another set of dependencies to keep current. Justified only
  because the connect path must run where Python and Node may not.
- **The render/decide rule is a discipline, not a mechanism.** Nothing stops a
  future contributor putting a decision in Go. The contract test above is the
  only structural defence; treat a decision appearing in Go as a bug.
- **Six release artifacts** need signing and checksums, and CI is currently
  disabled. That is phase 2 scope and should not be discovered late.
- **Scope check:** phase 2 is arguably itself two plans — the Go CLI, and the
  release pipeline. Decide that when phase 2 is planned, not now.
