# Architecture decision records

One decision per file, numbered. Each states context, the decision, and its
consequences — including what the decision makes worse, because every meaningful
technical choice makes something worse.

| # | Decision | Status |
|---|---|---|
| [0001](0001-mcp-surface-is-three-retrieval-tools.md) | The MCP surface is three retrieval tools | Accepted |
| [0002](0002-deterministic-controls-not-self-asserted-flags.md) | A self-asserted flag must not be a security boundary | Accepted |
| [0003](0003-one-page-contract-loaded-from-the-vault.md) | One page contract, loaded from the vault | Accepted |
| [0004](0004-the-audit-document-is-the-authority.md) | The audit document is the authority | Accepted |
| [0005](0005-corpus-repair-by-parser-revision-bump.md) | Repair the corpus by bumping `PARSER_REVISION` | Accepted, not executed |

Findings and their measurements live in `../AUDIT_2026-09-15.md`. Work state,
ownership and gates live in `.unlazy/full-fix-2026-09-15/PLAN.md`.
