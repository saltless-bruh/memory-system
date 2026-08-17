# GATE 3 — AGPL-3.0 License Decision Memo (T-0.3 → R-8.4.3)

> **This gate is a human decision, not a code task.** An agent cannot
> "confirm company policy." This memo frames the exact question so a
> maintainer can obtain a written answer and record it below.

## The question, precisely

> Does the company's OSS policy permit taking a runtime dependency on
> **`basic-memory`**, which is licensed **AGPL-3.0**, for an
> **internal, self-hosted** deployment that is **not** offered to third
> parties over a network?

## Why it matters here

- `basic-memory` is the **only AGPL component** in the stack. Everything
  else is permissive:
  | Component | License |
  |---|---|
  | basic-memory | **AGPL-3.0** ← the sole question |
  | Gitea | MIT |
  | RAG-Anything | MIT |
  | LightRAG | MIT |
  | LiteLLM | MIT |
- AGPL's defining clause (§13) triggers a source-provision obligation when
  you **convey the software to users interacting with it over a network**.
  A purely **internal** tool used by employees is generally **not** a
  "conveyance to the public." But "generally" is not "your company's
  counsel said so" — hence this gate.

## Scope of use in this project (facts for the reviewer)

- Deployment: self-hosted, on internal infrastructure (Docker Compose).
- Users: team members and their coding agents, internal only.
- Modification: we do **not** fork or modify basic-memory's source; we
  configure it and call its MCP/CLI.
- Distribution: the SNP Memory System is **not** distributed or sold to
  outside parties.

## The branch this gate controls

| Outcome | Consequence for the plan |
|---|---|
| **ALLOWED** | Proceed with `basic-memory` as primary engine. Continue T-0.4 → Phase 1 (T-1.x) as written. |
| **DENIED** | Skip the basic-memory branch entirely. **Scout-DIY becomes primary** (task T-2.4 promoted ahead of T-1.x). The vault, frontmatter contract, and Scout RAG-bridge are unchanged — that is exactly why the design kept engine swap at zero migration cost (design.md §2.2, §7). |

Because a DENIED outcome deletes a whole branch of work, **this is the
cheapest gate to resolve first** — it needs no infrastructure, only an
answer.

## Decision record

```
Decision:        [x] ALLOWED (provisional — build-to-evaluate)
Decided by:      Team lead (verbal), relayed by developer
Date:            2026-07-21
Basis:           Lead directed building V1 with basic-memory to evaluate
                 whether it fits the team's needs before committing.
Conditions:      Provisional. This authorizes an internal, self-hosted
                 build for evaluation — NOT a blanket policy sign-off for
                 production. Re-confirm with a formal OSS-policy check
                 before productionizing or exposing over a network to
                 outside parties. (AGPL §13 network clause does not apply
                 to internal-only self-host — see above.)
Notes:           basic-memory remains the sole AGPL dependency; the rest
                 of the stack is permissive. If the fit evaluation fails,
                 the Scout-DIY branch (T-2.4) replaces it with zero data
                 migration.
```

## Follow-through

- Record the same outcome one line in `spikes/GATE_RESULTS.md`.
- If DENIED/CONDITIONAL restricts network exposure, cross-reference the
  no-egress runbook item (T-3.7) so the constraint is operationalized.
