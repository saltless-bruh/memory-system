# ADR-0004 — The audit document is the authority; the proposals are side-sources

- **Status:** Accepted · 2026-09-15
- **Deciders:** repository owner

## Context

`REMEDIATION_PLAN.md`, `CLI_MCP_VOCABULARY` and `OBSERVABILITY` disagreed with
each other and, in several places, with the measured system. A docs-contract test
requires every `docs/proposal/*.md` to declare itself historical; all three
lacked the banner, and all three were untracked.

## Decision

`docs/AUDIT_2026-09-15.md` is the authority for findings and their disposition,
and continues until no flaw or conflict remains. The three proposals are
side-sources, banner-marked per file rather than blanket:

- `REMEDIATION_PLAN.md` → **DOMAIN REFERENCE, NOT SNP DEPLOYMENT AUTHORITY** — a
  live record, no longer authority.
- `CLI_MCP_VOCABULARY`, `OBSERVABILITY` → **SUPERSEDED PROPOSAL** — their
  ten-tool surface and O1–O5 ordering were specifically replaced.

## Consequences

- **Better:** one authority; the docs-contract test passes.
- **Worse:** the audit document is 1,300+ lines, untracked, and `origin` is
  public while it enumerates unpatched CVEs and vault structure. Its disposition
  is still an open owner decision.
- **Related:** a removed outcome is recorded in the plan inventory as
  `REMOVED_BY_USER` and its gate deleted, rather than left as a permanent
  `ABANDON:` implying work someone still owes.
