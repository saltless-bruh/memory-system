# Implementation Plan: Local MCP Server, Generated from the Registry

Branch: `fix/architecture-security-hardening` · Base: `dea240c`
Prepared: 2026-08-21 · Brainstorm: `artifacts/superpowers/brainstorm-mcp-2026-08-21.md`

> The previous occupant (Step 3 — Multi-Article Compilation, complete: 728 tests,
> 5 addresses PASS, 5 pages GROUNDED) was archived to
> `artifacts/superpowers/plan-step3-2026-08-21.md`.

---

### Goal

Let any MCP-speaking agent operate the memory system — verify, decompose, compile —
through tools generated from `registry.py`, **without** widening the authority the
`scout` retrieval boundary grants and **without** a second definition of any command.

**Definition of done:** an agent connects to `snpmemory mcp`, calls `verify`, gets a
structured finding; calls `plan_articles`, edits the plan, calls `compile_plan`, and polls
`compile_status` until N pages exist — while `scout/mcp_server.py` remains byte-identical.

---

### The constraint that shapes everything

`scout` runs in a container with **no read-write repository mount**, and
`docs/ARCHITECTURE_STATUS.md` lists such a mount among its **prohibited claims** — v2
hardening removed it deliberately. Seven of the eight CLI commands are
`Prerequisite.LOCAL` and need a checkout.

So the tools cannot live on `scout`. Not a preference: putting them there would restore the
topology the security work eliminated and would make a retrieval token a page-writing
token. They get a **local stdio server** instead, carrying exactly the authority of the
user who launched it.

---

### Assumptions

1. `scout/mcp_server.py` is **not modified**. One tool, `rag_fetch`, remains the only door
   into RAG.
2. `fastmcp==3.3.1` stays pinned. Its SDK speaks `2025-11-25`; the current spec is
   `2026-07-28`. We design so a later move is cheap rather than chasing a release candidate.
3. `Task*` types exist in the pinned SDK but in the **pre-redesign** form the `2026-07-28`
   RC reworked. We therefore use plain handle-returning tools, not native Tasks.
4. Tool descriptions come only from `registry.py` — never from document or user text.
5. PR-first (R-6.4/R-7.3) is unchanged: no tool pushes to `main`.

---

### Plan

**1. Archive Step 3's plan, land this one**
- Files: `artifacts/superpowers/plan.md`, `plan-step3-2026-08-21.md`
- Verify: `ls artifacts/superpowers/`; `head -4 plan.md`.

**2. Extract one shared invocation path**
- Files: `scout/cli/invoke.py` (new), `scout/cli/app.py`, `tests/test_cli_core.py`
- Change: lift `_wrap`'s body into `invoke(spec, *args, **kwargs) -> CommandResult` —
  load at call time, resolve config from the **declaration**, inject only if the function
  accepts it. `app.py` calls it; the MCP server will too.
- Why: two callers, one path. It is the mechanism that makes "cannot drift" true rather
  than aspirational, and it preserves the existing guarantee that `schema` cannot read a
  credential even by accident.
- Verify: `pytest tests/test_cli_core.py -q` unchanged; a new test asserts `invoke` passes
  no config to a `NONE` command.

**3. Declare the MCP exposure policy**
- Files: `scout/cli/mcp_policy.py` (new), `tests/test_mcp_policy.py` (new)
- Change: a small table deciding, per command, `EXPOSE` / `HIDE` / `GROUP(tool, stage)`.
  Initial policy — `schema` HIDE (an MCP client lists tools natively), the five verify
  commands GROUP into one `verify` tool with a `stage` argument, `plan-articles` and
  `compile-plan` EXPOSE.
- Why not pure 1:1: eight tools next to `rag_fetch` and basic-memory's tools is 12+
  definitions in every agent's context, and a bloated tool list measurably degrades tool
  selection. Five is enough.
- **Anti-drift guard:** a test asserts every `CommandSpec` in `REGISTRY` appears in the
  policy exactly once. Adding a command without deciding its exposure **fails the suite**.
- Verify: `pytest tests/test_mcp_policy.py -q`.

**4. Map results and exit codes onto MCP**
- Files: `scout/cli/mcp_result.py` (new), `tests/test_mcp_result.py` (new)
- Change: one function turning a `CommandResult` into an MCP tool return.
  - `0` → success payload.
  - `1` (semantic failure) → **successful tool call** carrying `{"status": "fail", ...}`.
    A finding is data an agent must reason about, not a transport error.
  - `2`–`7` → a tool **error**. Exit 2 must never look like a result, because
    `ci_address_gate.py`'s whole contract is that 2 never authorises mutation.
- Also: return summary fields by default, full detail behind `detail=true`.
- Verify: table-driven tests over every `ExitCode`; assert 2 never yields a success payload.

**5. Generate the tools**
- Files: `scout/mcp/__init__.py`, `scout/mcp/local_server.py` (new),
  `tests/test_local_mcp_server.py` (new)
- Change: build a `FastMCP` server by walking `REGISTRY` through the policy; derive each
  tool's name, description, and annotations from the `CommandSpec`.
  - `Effect.READ` → `readOnlyHint=True`
  - `Effect.WRITE` / `DESTRUCTIVE` → `destructiveHint=True`
  (all three hints exist in the pinned SDK — verified.)
- **Import purity:** building and listing tools must touch no database, gateway,
  credential, or running stack — the guarantee `schema` already makes.
- Verify: a test builds the server with sockets disabled and asserts the tool list, the
  annotations per tool, and that no import reached the network.

**6. Ship `snpmemory mcp`**
- Files: `scout/cli/commands/mcp.py` (new), `scout/cli/app.py`, `docs/CLI_SPEC.md`
- Change: a command that runs the stdio server. Declared `Prerequisite.LOCAL`,
  `Effect.READ` (the server itself reads; its tools declare their own effects).
- Verify: `snpmemory mcp --help`; an integration test drives one `tools/list` and one
  `verify` call over stdio and asserts a structured result.

**7. Handles for long-running compilation**
- Files: `scout/cli/tasks.py` (new), `tests/test_tasks.py` (new)
- Change: `compile_plan` runs pre-flight synchronously (retrieval only, seconds), then
  starts the batch as a detached subprocess and returns
  `{handle, articles, preflight}`. `compile_status(handle)` reports per-article progress.
- **The state already exists.** Step 3's staging directory and plan file are a durable,
  resumable, per-article progress record. The handle is derived from the plan path; status
  is computed by reading staging against the plan. A small `.run.json` (pid, started_at)
  distinguishes *in flight* from *stalled*.
- Verify: unit tests over a synthetic staging directory (2 of 4 staged → status reports 2
  done, 2 pending); a test asserts a stale `.run.json` with a dead pid reports `stalled`,
  not `running`.

**8. Require confirmation for mutation**
- Files: `scout/cli/commands/compile.py`, `scout/mcp/local_server.py`
- Change: `compile_plan` takes a required `confirm: bool`; false or absent returns a
  refusal naming what would be written. Current guidance is that mutation should prompt at
  call time, not rely on install-time consent; `destructiveHint` asks the client to prompt,
  and this makes it true even for clients that do not.
- Verify: a test asserts `confirm=False` writes nothing and returns a refusal.

**9. Documents**
- Files: `README.md`, `docs/CONNECT_AGENTS.md`, `docs/ARCHITECTURE_STATUS.md`, `AGENTS.md`
- Change: state which server does what; that `rag_fetch` remains the only door into RAG;
  and — plainly — that the local server **carries the launching user's authority and must
  not be exposed over HTTP without an auth design**.
- Verify: `grep` that no document describes the local server as remotely reachable.

**10. Full verification**
- `ruff check .` · `mypy scout scripts` · `pytest -q` (expect 728 + new, zero regressions)
- `scout/mcp_server.py` unchanged: `git diff --stat scout/mcp_server.py` is empty.
- Live: connect a real MCP client, list tools, run `verify`, then
  `plan_articles → compile_plan → compile_status` to completion.
- Confirm the 7 healthy containers are untouched by any of it.

---

### 2026 practice check (verified 2026-08-21)

**MP-1 — the current spec is `2026-07-28`; we are on `2025-11-25`.** It makes the core
stateless (no `initialize`, no `Mcp-Session-Id`), adds routable `Mcp-Method`/`Mcp-Name`
headers, moves **Tasks to an extension after a production redesign**, and deprecates
**Roots, Sampling and Logging** with a 12-month offramp.

Applied: we use none of the deprecated three — we pass explicit path parameters rather than
Roots, call LiteLLM directly rather than Sampling, and already log to stderr. That
alignment is partly luck; recording it means the eventual upgrade is a transport change,
not a redesign. We deliberately do **not** adopt the SDK's pre-redesign `Task*` types
(step 7).

**MP-2 — tool-list bloat degrades selection.** A tool definition costs ~100–500 tokens and
real deployments reach tens of thousands; bloated schemas make agents pick the wrong tool.
Guidance: keep parameters ≲8 per tool and return few fields by default.

Applied: step 3 collapses 8 commands to 5 tools; step 4 returns summary fields with detail
behind a flag.

**MP-3 — tool poisoning and rug pulls.** Tool descriptions and schemas are model-read
metadata that users rarely see; a later update can swap benign behaviour for malicious.

Applied: descriptions come only from `registry.py`, which is PR-reviewed, and step 5's test
pins the generated tool list so a change to it must be seen in review. Retrieved document
text never reaches a description.

**MP-4 — confused deputy and token passthrough.** Servers must act with the caller's
authority, not their own, and **must not accept tokens not issued for them**.

Applied: we add **no new network surface**, which is the strongest available answer — a
stdio server has no token to confuse. `scout` keeps its request-scoped verification
untouched. Step 9 writes the limitation down so nobody later exposes it over HTTP casually.

**MP-5 — mutation needs call-time approval**, not one-time install consent.

Applied: step 8's required `confirm`, plus `destructiveHint`.

Sources:
- [The 2026-07-28 MCP Specification Release Candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
- [MCP 2026-07-28 spec: every breaking change](https://stacktr.ee/blog/mcp-2026-spec-changes)
- [Security Best Practices — Model Context Protocol](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [MCP Security — OWASP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)
- [MCP Security Best Practices: A Practical Guide for 2026](https://blog.mcpservers.org/posts/mcp-security-best-practices)
- [10 strategies to reduce MCP token bloat](https://thenewstack.io/how-to-reduce-mcp-token-bloat/)
- [MCP Tool Schema Design Guide 2026](https://kansei-link.com/en/insights/mcp-tool-schema-design-guide-2026.html)
- [MCP tool design: practical approaches and tradeoffs — AWS](https://aws.amazon.com/blogs/machine-learning/mcp-tool-design-practical-approaches-and-tradeoffs/)

---

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| **A tool silently gains vault-write authority.** | Effects come from the registry, not the tool author; step 3's guard forces an explicit exposure decision per command; step 8 requires `confirm`. |
| **The local server gets exposed over HTTP later** "because it already works". | stdio only; step 9 states the authority model in the architecture document, where prohibited claims already live. |
| **Detached subprocess orphans or double-runs.** | The handle derives from the plan path, so two runs on one plan collide detectably; `.run.json` carries the pid and a dead pid reports `stalled`, never `running`. |
| **Registry and tools drift** — the exact failure `registry.py` was written to prevent. | Step 3's test fails the suite when a command has no exposure decision; step 5 pins the generated list. |
| **`2025-11-25` ages out.** | No deprecated features used; Tasks deliberately hand-rolled so the move to the extension is a re-point, not a rewrite. |
| **An agent treats exit 2 as a finding** and heals on infrastructure failure. | Step 4 maps 2–7 to tool errors, never results, with a table-driven test over every code. |
| **Import-time credential reads.** | Step 5 tests that building and listing tools touches no environment — the same structural guarantee `schema` has. |

---

### Rollback plan

- Everything new lives under `scout/mcp/`, `scout/cli/mcp_*.py`, `scout/cli/tasks.py`,
  `scout/cli/commands/mcp.py` and their tests. Deleting them removes the feature.
- Step 2 touches shipped code (`app.py`); it is a pure extraction with existing tests as
  the guard, and is independently revertible.
- `scout/mcp_server.py`, the deployed containers, and the database are untouched — step 10
  asserts the first of those mechanically.
- No migrations, no schema changes, no vault writes outside what `compile_plan` already did.

---

### Deferred, deliberately

- **A remote story for LOCAL commands.** Needs OAuth 2.1 with audience validation and a
  real deployment design — a project, not a flag.
- **Upgrading off `fastmcp==3.3.1`.** The pin exists for Streamable HTTP and output-schema
  stability, and `scout` depends on it.
- **Native Tasks**, until we move past `2025-11-25`.
- **MCP Apps** (interactive HTML in sandboxed iframes) — interesting for showing a compile
  in progress, out of scope here.
