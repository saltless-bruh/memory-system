# `snpmemory` — CLI Specification

> **Status: ACTIVE SPEC, partially implemented.** Dated 2026-08-20, revised
> 2026-08-24. `snpmemory schema` reports what actually exists; the tables below
> are the contract, including the commands still to be built.
> `tests/test_cli_schema_conformance.py` asserts that every implemented command
> appears here, so this document cannot drift ahead of the registry unnoticed.

## Why this exists

Today the system exposes **two MCP retrieval tools** (`wiki_search`, `wiki_read`)
and **21 CLI entry points**
reachable only as `python scripts/<name>.py` from inside a checkout. Two
consequences:

* The distributed agent package instructs agents to run `python scripts/mint.py`
  and ships no `scripts/` directory. Half the documented workflow is unreachable
  on a user's machine.
* Every future MCP tool would be a second implementation of something a script
  already does, free to drift from it.

`snpmemory` is one dispatcher with three surfaces: a human CLI, a CI gate, and
(later) MCP tools. The surfaces render differently; the logic is shared.

## Audiences, and what each one needs

| Audience | Needs |
|---|---|
| **Human** | readable text, colour when it helps, useful errors |
| **CI** | stable exit codes, no prompts, no colour in logs |
| **Agent** | structured output, discoverable capabilities, errors it can branch on |

Serving all three is what drives every rule below. This follows the conventions
in [The CLI Spec](https://clispec.dev/) where they do not conflict with contracts
this repository already relies on; divergences are stated explicitly.

**Conformance is checked, not claimed.** `snpmemory schema` emits a document
that validates against The CLI Spec **v0.3** (`clispec.dev/schema/v0.3.json`),
vendored at `tests/fixtures/clispec-v0.3.json` and asserted by
`tests/test_cli_schema_conformance.py`. The schema is vendored rather than
fetched because this suite blocks network access; a conformance test that
reaches the internet fails for reasons unrelated to the code.

---

## 1. Exit codes

Two are **outcomes** — the command ran correctly and is reporting what it found.
The rest are **errors** — the command could not complete. No code serves both,
and no two outcomes share a code.

| Code | Kind | Meaning |
|---|---|---|
| `0` | outcome | success |
| `1` | outcome | **semantic failure** — the check ran and found real problems (address drift, unsupported claim, lint error) |
| `2` | error | **infrastructure / configuration** — database, gateway, or credentials unavailable |
| `3` | error | **input validation** — malformed arguments, a path outside `raw/`, an unknown department |
| `4` | error | **auth** — missing/invalid token, or requested scope exceeds the caller's |
| `5` | error | **confirmation required** — destructive action without `--yes` |
| `6` | error | **tty required** — interactive input needed with no TTY attached |
| `7` | error | **conflict** — page already exists, protected branch, pre-staged work |

**`0` / `1` / `2` are inherited, not invented.** Seven scripts, `ci_address_gate.py`,
`AGENTS.md` and `README.md:201` already depend on them — notably *"Exit `2` never
triggers mutation"*, which is a safety property, not a formatting choice. The
prevailing external convention assigns `2` to auth; this repo cannot adopt that
without breaking a working merge gate, so **auth is `4`** and the existing
contract is preserved. Codes `3`–`7` are new and collide with nothing.

**`2` must never trigger mutation.** Any command that heals, writes, or commits
treats `2` as a full stop.

## 2. Output

**`--output` / `-o`**, taking `auto` · `text` · `json` · `yaml`. `--format` is
accepted as an alias.

* An explicit `-o` **always wins** over TTY detection.
* **`auto` resolves to `text`, including when piped.** This is a deliberate
  divergence: existing CI steps and agent workflows read the text output of
  these scripts, and silently switching to JSON when piped would break them.
  Machines ask for `-o json` explicitly.

  The spec permits this exactly once — *"a tool that keeps a human-readable
  default MAY do so if it declares that default in the top-level `output`
  field"* — so `schema` emits `"output": {"tty": "text", "piped": "text"}`.
  That declaration is what makes the divergence conformant instead of silent,
  and a consumer reads it rather than discovering the default by piping.
* Every command supports every format. Not just the interesting ones — an agent
  has no way to know which commands were considered interesting.

**Stream discipline, absolute and in every mode:**

```
stdout  →  data only
stderr  →  progress, diagnostics, errors
```

Exit `0` means stdout is trustworthy. Non-zero means stderr explains why.

**Colour:** auto-detect with `isatty()`. No ANSI when stdout is not a TTY.
`NO_COLOR` is honoured. `--no-color` forces it off.

## 3. Errors

In **text** mode: a human-readable message on stderr.

In **structured** mode: the same error, as a JSON envelope written as the **last
line of stderr** —

```json
{"kind": "auth",
 "message": "requested departments exceed authenticated scope",
 "hint": "the token grants [infra]; request a subset or use a broader token",
 "details": {"requested": ["redteam"], "granted": ["infra"]},
 "retryable": false}
```

`kind` and `message` are required; `hint`, `details`, `retryable` are optional.
`kind` is drawn from the error names in §1, so an agent can branch on the code
*or* the name without parsing prose.

**Secrets are never printed** — not in `details`, not in a hint, not in a
traceback. This repository redacts matched values even in its own secret
scanner's diagnostics; the CLI holds the same line.

## 4. Discovery

```
snpmemory schema            # machine-readable description of every command
```

```
snpmemory schema <command>  # the same document, narrowed to one command
```

Emits commands, arguments, cardinality, output shape, and the error kinds each
command can raise. **Agents should never need to parse `--help`.**

That principle is load-bearing, and it is why every command declares `args` and
`output_fields`. A command that takes flags and declares none teaches an agent
that it takes none — worse than saying nothing. Command-level `errors` and
`outcomes` are *references* into the tool-level tables, so an exit code is
resolved in one place rather than restated per command and left to drift; the
resolved codes are repeated under `error_codes` / `outcome_codes` for a reader
that would otherwise have to join the tables itself.

Narrowing keeps the tool-level tables, because a consumer reading one command
still has to resolve the error kinds it references. An unknown name exits `3`;
it never returns an empty document, which would turn a typo into "that command
does not exist".

`schema` must work **before anything else does** — no authentication, no config
file, no network, no running stack. It is how a tool server learns what to
expose, which makes the future MCP layer close to mechanical: tool definitions
are generated from the same declarations the CLI already publishes.

## 5. Interaction

**Prompting is never the default path.** It is decoupled from normal execution
and reachable only on request. Resolution order, applied by every command that
could ask a question:

1. **An explicit argument wins.** `--client cursor`, `--config-path …`, `--yes`
   — if the answer was supplied, nothing is asked, in any environment.
2. **A sensible default is used** where one genuinely exists.
3. **`--interactive` prompts** — and only then. Without the flag the command
   never asks, even attached to a terminal. A TTY is permission to *render* a
   prompt, not a reason to need one; some agents allocate a pty and would sit in
   front of a menu they cannot answer.
4. **Otherwise it refuses**, naming the flag that would have supplied the answer:
   * needed an answer, no `--interactive` → exit `3`, hint names the argument
   * `--interactive` given but no TTY → exit `6`, hint names the argument

* **Every interactive input has a flag alternative.** No exceptions.
* Destructive actions require `--yes`; without it, exit `5` and name the flag in
  the `hint`. Do not proceed silently — an agent should hit a wall, not a trigger.

**Worked example — `scripts/export_mcp_config.py`.** It already guards its
prompt with `sys.stdin.isatty()` and errors cleanly when piped, so it does not
hang. Two things still fail this rule: the prompt is the default path whenever a
TTY is present rather than requiring `--interactive`, and `parser.error()` exits
with argparse's `2`, which in §1 means *infrastructure failure* — a missing
argument is `3`. Both must be corrected before it becomes `snpmemory mcp-config`.

---

## 6. Commands

Flat, no subcommand groups. Two prerequisite classes, and the CLI states which
when it cannot meet them.

**Remote** — needs only a server URL and token; works anywhere the package is
installed.

*The authenticated Scout MCP server remotely exposes `wiki_search` and
`wiki_read`. The commands below remain local operator entry points over the same
engine and canonical envelope.*

**Local** — needs a repository checkout.

| Command | Purpose | Notable codes |
|---|---|---|
| `snpmemory search <query> --dept <dept> [--limit] [--seen]` | rank distinct vault pages through the same pgvector engine the agent uses. `score` is an RRF weight, never a similarity | `3` invalid department |
| `snpmemory read <page> --dept <dept> [--mode full\|tldr\|outline] [--section]` | read the current file as the same canonical envelope the agent receives | `3` ambiguous title / section |
| `snpmemory fetch --path --hint [--loc] --dept [--k]` | verbatim evidence | `1` no source · `3` unknown department |

| Command | Purpose | Notable codes |
|---|---|---|
| `snpmemory mint --path --hint --dept --loc` | mint a verify-PASS address | `1` no hint works |
| `snpmemory compile --path --title --category --dept --loc` | compile a draft page | `7` page exists / protected branch |
| `snpmemory plan-articles <path> --dept [--category] [--max-depth] [--out]` | propose a decomposition from the source's own headings; no model call, byte-stable output | `1` no numbered headings |
| `snpmemory compile-plan <plan> --confirm [--background] [--dry-run] [--no-resume] [--allow-uncertain]` | compile every article in an approved plan; writes nothing unless all pass | `1` an article cannot mint or ground · `5` no `--confirm` |
| `snpmemory propose --page` | PR-first commit | `7` pre-staged work |
| `snpmemory ingest --path │ --dir` | index into pgvector | |
| `snpmemory extract --path │ --dir` | figures + tables → `derived/` | `3` path outside `raw/` |
| `snpmemory compile-status <handle>` | progress of a background batch | `1` stalled / not started / failed / cancelled |
| `snpmemory compile-cancel <handle>` | ask a running batch to stop at its next article boundary | `3` unknown handle |
| `snpmemory mcp [--root] [--list-tools]` | serve these operations to an agent over stdio | `3` `--root` is not a checkout |
| `snpmemory verify-vault` | frontmatter + index lint | `1` lint errors |
| `snpmemory verify-addresses` | address merge gate | `1` drift/fail |
| `snpmemory verify-groundedness` | faithfulness gate | `1` unsupported claims |
| `snpmemory verify-secrets` | secret scan | `1` findings |
| `snpmemory check` | all four verifies, in order, first failure wins | |
| `snpmemory heal` | apply scoped address heals | |
| `snpmemory gate --mode pr│scheduled` | closed-loop CI state machine | |
| `snpmemory up │ down │ status │ logs [service]` | stack lifecycle | `2` docker unavailable |
| `snpmemory init` | bootstrap secrets and `.env` | |
| `snpmemory install-agent [dir] [--dry-run] [--confirm]` | install the agent package | `3` target is not a directory · `5` target already has `.agent/` |
| `snpmemory mcp-config --client [--out] [--confirm]` | emit MCP client configuration | `5` `--out` exists · `7` target unparseable |
| `snpmemory schema` | capability description | |

`up`/`down`/`status`/`logs` shell out to `docker compose` and forward unknown
arguments, so `snpmemory up --build` behaves as expected. They are a
convenience over a tool people already know — not a replacement for it.

### Why `search`, `read` and `fetch` are Local, not Remote

They were specified as Remote. Implemented, all three answer from the checkout:

* `read` uses `ScoutDiyEngine.wiki_read`, which parses the current page file. A
  page **is** a file in `wiki/`; the index is never used to reconstruct it.
* `search` uses `ScoutDiyEngine.wiki_search` over the shared PostgreSQL index,
  so the CLI and authenticated Scout surface have identical ordering.
* `fetch` calls `scout.core.rag_fetch` — the same function the Scout MCP server
  used before V3 — against the RLS backend. It remains an operator diagnostic
  and is hidden from both agent tool surfaces.

The tradeoff this accepts: none of the three CLI commands is usable from a
machine with no checkout. That audience uses the authenticated Scout server.
`scout/cli/mcp_policy.py` exposes local `search`/`read` under the same
`wiki_search`/`wiki_read` names and keeps direct `fetch` hidden.

## 7. Architecture

Commands **return** structured results; they do not print.

```
scout/cli/commands/*.py  →  CommandResult(exit_code, kind, data, messages)
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
        snpmemory CLI              MCP tools                   CI
     renders text/json/yaml      returns data              reads exit code
```

One implementation, three surfaces, no drift. This is the whole reason the CLI
comes before the MCP server rather than after it.

**Framework: `cyclopts`.** Already present at 4.22.4 as a transitive dependency
of the pinned `fastmcp`, so it adds nothing to the dependency tree, and it is
the CLI layer that library already uses. `rich` (15.0.0) and `click` (8.4.2) are
likewise already available. Guidance is to choose a multi-command framework
before writing the second command; at roughly twenty commands `argparse` is the
wrong tool.

**Old paths keep working.** `scripts/*.py` remain as thin shims delegating to the
dispatcher, so `.gitea/workflows/` and the installed agent workflows do not break
on this refactor, and the migration stays reversible.

## 8. Acceptance

The implementation is done when:

- [ ] `pip install -e .` puts `snpmemory` on PATH via `[project.scripts]`
- [ ] every command supports `-o text│json│yaml`
- [ ] stdout carries data only, in every mode
- [ ] exit codes match §1, and `2` never mutates
- [ ] structured mode emits the §3 envelope as the last line of stderr
- [ ] no ANSI on a non-TTY; `NO_COLOR` honoured
- [ ] `snpmemory schema` runs with no auth, no config, no network
- [ ] no command prompts without `--interactive`, and none blocks without a TTY
- [ ] no command exits with argparse's default `2` for a missing argument
- [ ] `scripts/*.py` shims keep existing CI green
- [ ] a test asserts no secret value reaches stdout or stderr in any mode

---

## References

- [The CLI Spec](https://clispec.dev/) — exit-code kinds, `--output`, error envelope, `schema`
- [Designing a CLI for AI agents](https://blog.arcjet.com/designing-a-cli-for-ai-agents/)
- [The `--json` pattern](https://www.gibil.dev/blog/cli-json-pattern)
- [CLI error messages as a dual-consumer problem](https://zircote.com/blog/2026/04/cli-error-messages-are-a-dual-consumer-problem/)
