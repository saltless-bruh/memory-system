# ADR-0001 — The MCP surface is three retrieval tools

- **Status:** Accepted · 2026-09-15
- **Deciders:** repository owner
- **Supersedes:** `REMEDIATION C2a` (7 tools), `CLI_MCP_VOCABULARY §4` (10 tools)

## Context

Three proposal documents specified three different MCP surfaces — 7 tools, 10
tools, and a third naming scheme — and the handoff listed the surface under
"decisions already made, do not relitigate". The owner confirmed it was in fact
open. Nine separate conflicts were resolved by measurement and ruling.

Separately, `mcp_policy.py` states that a tool definition costs 100–500 tokens of
every agent's context on every turn, and that long tool lists measurably degrade
tool selection.

## Decision

The served surface is **three retrieval tools**: `wiki_search`, `wiki_read`,
`wiki_quote`.

Everything else was argued off the surface by a criterion set at the time:

| moved to | what | why |
|---|---|---|
| skills | `wiki_verify`, `plan-articles`, `compile ×3` | an authoring agent needs a checkout to commit, and a checkout carries the skill; a tool would cost every agent context to reach agents that already have it |
| CI only | `verify-vault`, `verify-secrets`, `verify-addresses`, `verify-groundedness`, `check` | whole-vault health is CI's job; never run on demand |
| later | `stack_health`, then `wiki_trace` | deferred pending the `sync_events` table |
| dropped | `stack_logs` | needs the docker socket — see ADR-0002 |

## Consequences

- **Worse:** `wiki_plan` leaves the surface carrying an unfixed defect — it hangs
  on PDF input via vision extraction. Dropping the tool does not fix the hang;
  it moves to a skill and hangs there.
- **Worse:** the `stack_` prefix is reserved with no members until observability
  lands, so the naming rule rests on one worked example.
- **Better:** the surface matches what `AGENTS.md` §2 already says Scout is —
  find a page, read it, read its source.
- **Note:** the three-tool count was reached by attrition, one ruling at a time,
  and never confirmed as a whole. Recorded here so it is a decision rather than
  an accident.
