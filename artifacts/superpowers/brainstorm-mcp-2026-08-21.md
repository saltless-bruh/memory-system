# Brainstorm: the SNP MCP Server

Prepared: 2026-08-21 · Branch: `fix/architecture-security-hardening` · Base: `dea240c`

### Goal

Expose the system's own capabilities — verification, decomposition, compilation — to any
MCP-speaking agent, so an agent can *operate* the memory system through tools rather than
by shelling out to `snpmemory`, without widening the authority the current retrieval
boundary grants.

---

### Constraints

- **`registry.py` already declares the contract.** Its docstring commits to generating MCP
  tool definitions from the same `CommandSpec` records "rather than written a second time
  and left to drift." A hand-written tool list would break a promise already in the repo.
- **`fastmcp==3.3.1` is pinned**, and the SDK it carries speaks **`2025-11-25`**
  (`DEFAULT_NEGOTIATED_VERSION` is `2025-03-26`). The current spec is **`2026-07-28`**.
- **Most commands are `Prerequisite.LOCAL`** — they need a repository checkout on disk.
- **`docs/ARCHITECTURE_STATUS.md` explicitly prohibits** "a developer checkout mounted
  read-write at `/repo`" as a claim about the current system.
- Scope rules are invariant: `Scope.departments` from the verified caller; a tool argument
  may narrow, never widen; document ACL `all` is not caller authority.
- R-8.5: retrieved text is data, never instructions.
- PR-first (R-6.4/R-7.3): no tool may push to `main`.

---

### Known context

**What exists today**

| Server | Where | Tools | Auth |
|---|---|---|---|
| `scout` | container, Streamable HTTP :8080 | exactly **1** (`rag_fetch`) | FastMCP-native, request-scoped JWT/static |
| `basic-memory` | container :8765 | third-party (`search_notes`, `read_note`) | none of ours |

`scout/mcp_server.py` is 137 lines and deliberately narrow: one tool, auth verified before
the function body runs, backend lifespan managed by FastMCP.

**What the CLI now offers** (8 commands, all declared in `scout/cli/app.py`):
`schema` (NONE), `verify-vault`, `verify-secrets`, `verify-addresses`,
`verify-groundedness`, `check` (all LOCAL/READ), `plan-articles` (LOCAL/READ),
`compile-plan` (LOCAL/**WRITE**).

**The fact that decides the architecture:** `scout` runs in a container with no
read-write repository mount, and mounting one is a prohibited claim. **The LOCAL commands
therefore cannot be served by the existing scout server** without changing the deployment
topology the whole security model rests on.

---

### Risks

- **Authority expansion.** `scout` today is a read-only, department-scoped retrieval
  boundary. Adding vault-mutating tools to it puts `compile-plan` behind the same door as
  `rag_fetch`. A token minted for retrieval would become a token that can write pages.
- **Confused deputy.** If a server executes with its own privileges rather than the
  caller's, callers reach resources they should not. `scout` gets this right today; a new
  server must not regress it.
- **Token passthrough is explicitly forbidden** — a server must not accept tokens not
  issued for it. Any new HTTP surface must validate audience, not forward.
- **Tool poisoning / rug pull.** Tool descriptions are model-read metadata. Ours come from
  `registry.py` and are PR-reviewed, which is the right posture — but only if descriptions
  keep coming from there and never from user or document text.
- **Long-running tools.** `compile-plan` takes minutes (2 generations + 1 judge per
  article, plus free-tier rate limiting). A naive synchronous tool call will time out and
  the agent will retry — re-burning quota the checkpoint was built to protect.
- **Token bloat.** Naively mapping 8 commands → 8 tools, next to `rag_fetch` and
  basic-memory's tools, is ~12+ definitions in every agent's context.
- **Protocol drift.** Building against `2025-11-25` while `2026-07-28` deprecates Roots,
  Sampling and Logging and makes the core stateless.

---

### Options

#### Option A — Add the tools to the existing `scout` server

**Summary.** Register the CLI commands as additional tools on `scout/mcp_server.py`.

- **Pros.** One endpoint; auth, transport and deployment already solved; agents already
  connect to it.
- **Cons.** Requires mounting the repo read-write into the scout container — a prohibited
  claim in `ARCHITECTURE_STATUS.md`, and the exact topology the v2 hardening removed. It
  fuses a network-exposed retrieval boundary with vault mutation, so one leaked token
  writes pages. Blast radius of any bug in the compile path now includes the RAG service.
- **Complexity** low · **Risk** **high** — architectural regression.

#### Option B — A second, local **stdio** MCP server generated from `registry.py`

**Summary.** `snpmemory mcp` starts a stdio MCP server on the developer's machine,
generating tools from `CommandSpec` records. `scout` stays exactly as it is.

- **Pros.** LOCAL commands get a local host, which is what they actually need — no repo
  mount, no new network surface, no new auth story to get wrong. Authority is exactly the
  authority of the user who launched it, which is honest and easy to reason about.
  `Effect` maps directly onto MCP `ToolAnnotations` (`readOnlyHint`, `destructiveHint`,
  `idempotentHint` — all present in the pinned SDK). Fulfils `registry.py`'s stated promise.
  Agents keep using `scout` for retrieval, unchanged.
- **Cons.** Two servers for an agent to configure. Not usable remotely. Local server holds
  the developer's full filesystem authority — must never be exposed over HTTP without a
  real auth design.
- **Complexity** medium · **Risk** low.

#### Option C — Option B, plus a **task-handle pattern** for long-running compilation

**Summary.** Option B, and `compile-plan` does not block. It returns a handle after
pre-flight; `compile-status` reports progress; the agent polls.

**The insight:** the staging directory and plan file **already are a task store.** Step 3
built checkpoint/resume for quota reasons, and that same mechanism is exactly what a task
handle needs — durable, resumable, inspectable per article. There is nothing new to
persist.

- **Pros.** No client timeouts, no retry storms re-burning quota. Maps cleanly onto the
  `2026-07-28` **Tasks extension** (`tools/call` returns a handle; the client drives
  `tasks/get`/`tasks/cancel`) whenever we adopt it. An agent can watch a long compile the
  way a human watches CI.
- **Cons.** More moving parts; polling semantics to define; the SDK's `Task*` types are the
  *pre-redesign* form, so using them natively now means adopting an API the RC has already
  reworked.
- **Complexity** medium-high · **Risk** low-medium.

#### Option D — Defer; document the CLI as the agent interface

**Summary.** Ship nothing; agents call `snpmemory ... -o json`.

- **Pros.** Zero new code, zero new attack surface; `schema` already describes everything.
- **Cons.** Requires shell access, which is precisely the authority MCP exists to avoid
  granting. Leaves `registry.py`'s promise unfulfilled. Does not serve agents that have MCP
  but no terminal.
- **Complexity** none · **Risk** low, but the goal goes unmet.

---

### Recommendation

**Option C — a local stdio server generated from `registry.py`, with a task-handle pattern
for compilation — built in two stages: B first, then C.**

Why:

1. **The repo-mount constraint decides it.** LOCAL commands need a checkout; the scout
   container must not have one. That rules out Option A on architecture, not preference.
2. **It keeps the two authorities separate.** `scout` stays a narrow, network-exposed,
   department-scoped *read* boundary with exactly one tool. Vault mutation lives on the
   developer's machine under the developer's own authority. A leaked scout token still
   cannot write a page.
3. **`registry.py` was designed for this.** Generating from `CommandSpec` is a promise the
   codebase already made, and it means `schema`, the CLI and MCP cannot drift.
4. **`Effect` → `ToolAnnotations` is free.** `READ → readOnlyHint`,
   `WRITE`/`DESTRUCTIVE → destructiveHint`. Confirmation-before-mutation is 2026 best
   practice and we already have the metadata to drive it.
5. **The task store already exists.** Step 3's staging + checkpoint is the durable
   progress record a task handle needs. Building C means exposing what is already there.

**Stage it:** land B (tools + annotations + schema generation) and prove it against a real
agent, then add C's handles for `compile-plan` only. Do not pursue the SDK's native `Task*`
types yet — they are the pre-redesign form; use plain handle-returning tools that can be
re-pointed at the Tasks extension when we move off `2025-11-25`.

**Tool surface — do not map 1:1.** Collapse the five verify commands into one
`verify` tool with a `stage` parameter (the `check` aggregate is already that idea), giving
roughly: `verify`, `plan_articles`, `compile_plan`, `compile_status`, `schema`. Five tools,
not eight. Keep parameters ≤8 per tool, and return the summary fields by default with
detail behind a flag.

---

### Acceptance criteria

- [ ] `snpmemory mcp` starts a stdio MCP server; `scout/mcp_server.py` is **unchanged**.
- [ ] Every tool is generated from a `CommandSpec`; adding a command to `registry.py` adds
      a tool with no second edit. A test asserts registry and tool list cannot diverge.
- [ ] `Effect.READ` tools carry `readOnlyHint: true`; `WRITE`/`DESTRUCTIVE` carry
      `destructiveHint: true`.
- [ ] The server imports and lists tools **without** a database, gateway, credential, or
      running stack — the same guarantee `schema` already makes.
- [ ] Exit codes survive translation: a semantic failure (1) is a tool *result* the agent
      can reason about; an infrastructure failure (2) is a tool *error*. Exit 2 never
      appears as a successful result.
- [ ] `compile_plan` returns within seconds with a handle; `compile_status` reports
      per-article progress read from the staging directory.
- [ ] Retrieved document text is never interpolated into a tool description or schema.
- [ ] No tool can push to `main`, and no tool accepts a department not in the caller's set.
- [ ] Documented plainly: this server carries the launching user's authority and must not
      be exposed over HTTP without an auth design.
- [ ] `README`/`CONNECT_AGENTS` state which server does what, and that `rag_fetch` remains
      the only door into RAG.

---

### Open questions for the owner

1. **Two servers, or one endpoint later?** Recommendation keeps them separate. A future
   remote story for LOCAL commands needs OAuth 2.1 with audience validation — a real
   project, not a flag.
2. **Upgrade `fastmcp` off `2025-11-25`?** The pin exists for "Streamable HTTP transport
   and MCP output schema stability", and `scout` depends on it. `2026-07-28` is a release
   candidate with a stateless core. My inclination is to stay pinned and design so the move
   is cheap, not to chase an RC.
3. **Does `compile_plan` need human approval per run?** 2026 guidance says mutation should
   prompt at call time, not at install time. The client controls that, but we can make it
   unmistakable via `destructiveHint` plus a required `confirm` parameter.
