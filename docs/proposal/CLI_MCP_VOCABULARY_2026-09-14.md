# One vocabulary for the CLI, the MCP surface, and the skills

> **SUPERSEDED PROPOSAL.** Preserved for design history. Its ten-tool surface
> was resolved to three retrieval tools on 2026-09-15; see
> `docs/AUDIT_2026-09-15.md` and `.unlazy/full-fix-2026-09-15/SPEC.md`.

**Proposal, 2026-09-14. Nothing implemented.** Written after an audit that found
two words each meaning three things, one operation carrying three names, and a
shipped skill instructing a violation of the rule shipped beside it.

Decisions already taken by the owner:

- content prefix is **`wiki_`** (matches the directory, and the two live tool
  names already use it)
- **`stack_`** earns a second prefix — troubleshooting is its own domain

---

## 1 · What is wrong today

### Two words, three referents each

```
  "scout"      the whole Python codebase   scout/
               ONE remote MCP server       scout
               a Docker image              snp-scout

  "snpmemory"  the CLI binary
               the local MCP server        SERVER_NAME = "snpmemory"
               what the system sounds like it is called
```

`scout/cli/` is the CLI's source living inside a module named after a different
server. Neither word can be used in a sentence without ambiguity.

### One operation, three names

| CLI | MCP tool | skill |
|---|---|---|
| `search` | `wiki_search` | `snp-query-wiki` |
| `read` | `wiki_read` | `snp-read-wiki-page` |
| `verify-vault` | `verify(stage=vault)` | `snp-verify-vault` |
| `compile-plan` | `compile_plan` | `snp-compile-wiki` |

### Six of the fourteen needed commands collide with agent built-ins

`read` · `fetch` · `status` · `logs` · `check` · `search` collide with an
agent's own `read`, `webfetch`, `grep`, `websearch`. Three of them
(`fetch`, `status`, `logs`) are ones this proposal wants to **expose** —
exposing them unprefixed would degrade tool selection, which is the exact
failure `mcp_policy.py` already warns about.

### Exposure was classified by family name, not by declared effect

`status` and `logs` are declared `read`. They are hidden under *"lifecycle is an
operator decision"* — the justification for `up` (`write`) and `down`
(`destructive`). Reading logs is not lifecycle. An agent asked *"why did my edit
not sync?"* currently has no tool that can answer.

---

## 2 · Three noun groups, matching the three audiences

```
  wiki    the knowledge content      what the vault holds
  stack   the running system         troubleshooting and lifecycle
  agent   connecting a client        setup, config, serving
```

These are the same three audiences the system already serves — the person
asking, the person operating, the person joining. The CLI gains a noun, the MCP
surface gains a prefix, and the skill takes the same word.

---

## 3 · The rename table — all 26 commands

### wiki — content (12 commands → 8 tools)

| today | CLI becomes | MCP tool | exposed |
|---|---|---|---|
| `search` | `wiki search` | `wiki_search` | ✅ **name unchanged** |
| `read` | `wiki read` | `wiki_read` | ✅ **name unchanged** |
| `fetch` | `wiki quote` | `wiki_quote` | ✅ **newly exposed** |
| `verify-vault` | `wiki verify --stage vault` | `wiki_verify(stage)` | ✅ |
| `verify-addresses` | `wiki verify --stage addresses` | ↑ same tool | ✅ |
| `verify-groundedness` | `wiki verify --stage sources` | ↑ same tool | ✅ |
| `verify-secrets` | `wiki verify --stage secrets` | ↑ same tool | ✅ |
| `check` | `wiki verify --stage all` | ↑ same tool | ✅ |
| `plan-articles` | `wiki plan` | `wiki_plan` | ✅ |
| `compile-plan` | `wiki compile` | `wiki_compile` | ✅ |
| `compile-status` | `wiki compile-status` | `wiki_compile_status` | ✅ |
| `compile-cancel` | `wiki compile-cancel` | `wiki_compile_cancel` | ✅ |

### stack — the running system (2 exposed, 3 CLI-only)

| today | CLI becomes | MCP tool | exposed |
|---|---|---|---|
| `status` | `stack health` | `stack_health` | ✅ **newly exposed** |
| `logs` | `stack logs` | `stack_logs` | ✅ **newly exposed** |
| `up` | `stack up` | — | ❌ `write` |
| `down` | `stack down` | — | ❌ `destructive` |
| `init` | `stack init` | — | ❌ generates credentials |

### agent — connecting a client (3, none exposed)

| today | CLI becomes | why never a tool |
|---|---|---|
| `install-agent` | `agent install` | writes an operating contract into a directory the user owns |
| `mcp-config` | `agent config` | an agent reading it is already connected |
| `mcp` | `agent serve` | the server cannot serve itself |

### Not exposed, kept as CLI (3)

| today | CLI becomes | why |
|---|---|---|
| `ingest` | `wiki ingest` | sync-job watches `raw/` already |
| `ingest-wiki` | *folded into* `wiki ingest` | same watcher, whole-vault variant |
| `propose` | `wiki propose` | R-7.3 — an agent opening its own PR reviews its own work |

### Dropped (2)

| today | why |
|---|---|
| `compile` | `compile-plan` covers one article as well as many; two compile commands invite the wrong pick |
| `mint` | `compile-plan` mints every address it needs; a standalone sub-step nobody drives |

### Unchanged (1)

`schema` stays top-level and CLI-only — an MCP client lists tools natively.

---

## 4 · The resulting MCP surface: 10 tools

```
  wiki_search            find which page          (unchanged)
  wiki_read              read that page           (unchanged)
  wiki_quote             verbatim source passage  NEW
  wiki_verify(stage)     lint: vault|addresses|sources|secrets|all
  wiki_plan              decompose a source, no model call
  wiki_compile           write pages, confirm=true
  wiki_compile_status    poll a batch
  wiki_compile_cancel    stop a batch
  stack_health           is the system serving?   NEW
  stack_logs             what did it say?         NEW
```

Ten definitions, up from six — but three of the additions replace nothing and
the verify family stays collapsed. Every name carries its domain, so none
competes with an agent's own `read`, `webfetch` or `grep`.

---

## 5 · What breaks, and what does not

**Does not break:**

- `wiki_search` and `wiki_read` keep their exact names. Both demo workspaces,
  `~/snp-demo/vault.opencode.json`, the rehearsal evidence and every recorded
  trace stay valid.
- Nothing renamed here is both exposed and working today. Everything gaining a
  new name is currently hidden, broken, or CLI-only.

**Breaks:**

- Operator muscle memory: `snpmemory search` → `snpmemory wiki search`.
- `docs/DEMO_PLAYBOOK.md`, `docs/runbook.md`, `docs/CLI_SPEC.md` and the seven
  skills carry old command spellings.
- `packages/snp-agent/` and its two mirrors `.agent/` `.claude/` must be
  regenerated and re-verified.

**Deliberately not in scope:** renaming the `scout/` Python module or the
`snpmemory` binary. Both are worth doing and both are a bigger blast radius than
this. This proposal fixes the *vocabulary an agent and an operator see*; the
module layout is a separate change.

---

## 6 · The guard that stops this recurring

The root cause was classification by family name rather than declared effect. A
test should make that impossible:

> **No command whose `effect` is `read` may be hidden for a lifecycle reason.**
> Exposure decisions must cite the command's own effect. A `read` command hidden
> as `write`/`destructive` fails the suite.

Plus the invariant already in `mcp_policy.py` — every declared command appears
in the policy exactly once — extended to: **every exposed tool name carries a
domain prefix.**

---

## 7 · Suggested order

| Phase | What | Risk |
|---|---|---|
| **A** | Expose `fetch`/`status`/`logs` under the new names; fix the three reason strings; add the effect-vs-exposure test | low — three additions, no renames |
| **B** | Rename CLI to noun groups, keep old spellings as deprecated aliases for one release | medium — docs and skills follow |
| **C** | Rename the remaining MCP tools; regenerate the agent package and mirrors | medium — coordinate with client configs |
| **D** | Reconcile the four operator-facing skills: `snp-bootstrap-system` and `snp-ingest-raw-data` describe work an agent has no tool for and the shipped rule forbids. Make them operator docs, or give them tools. | needs a decision |

Phase A is worth doing on its own even if B–D never happen: it closes the
troubleshooting gap with no renames.

---

## Appendix — the skill contradiction, for phase D

`packages/snp-agent/skills/snp-bootstrap-system/SKILL.md` instructs:

```
  ./scripts/bootstrap.sh
  docker compose up -d --build
  docker compose ps
```

`packages/snp-agent/rules/snp-memory.md:9` states:

> Do not read the vault through the filesystem, shell, or PostgreSQL.

`up`, `down` and `status` are hidden from MCP, so an agent following the skill
has no tool and must shell out. The package ships a skill instructing a
violation of the rule it ships beside it. Four of the seven skills
(`snp-bootstrap-system`, `snp-ingest-raw-data`, `snp-export-mcp`,
and partly `snp-verify-vault`) describe operator work rather than agent work.
