# Brainstorm: Architecture and Security Hardening Plan Rewrite

Date: 2026-08-18

## Goal

Turn Gemini's revised draft into an execution-ready remediation plan that closes the audited security, data-loss, reliability, authoring-contract, CI, testing, and documentation gaps without changing implementation code during planning.

## Constraints

- Credential rotation is an external incident-response action and must occur before repository cleanup; a Git history rewrite requires separate explicit approval.
- All implementation work belongs on `fix/architecture-security-hardening`, followed by a pull request and human merge.
- MCP authorization must derive from verified server-side identity. Caller parameters may narrow but never create privileges.
- PostgreSQL hardening must upgrade existing persistent volumes as well as fresh installations.
- Host-sync must never mount or mutate the developer repository.
- Wiki changes must satisfy the exact `AGENTS.md` frontmatter/body/address/PR contract.
- Unit tests must remain offline; service-backed tests must fail clearly when prerequisites are absent.
- `raw/` evidence and historical documents must not be rewritten merely to make current documentation searches clean.

## Architectural decisions

- Use explicit Scout auth modes: `jwt` (default and fail-closed), `static` (single-tenant server scope), and `development` (explicit local-only mode).
- Use FastMCP's JWT verifier and request-scoped access token rather than manually decoding bearer tokens.
- The only page departments are `redteam`, `blueteam`, `ai_eng`, and `infra`. Raw-document ACL value `all` means public to any authenticated department; it is never accepted as a caller department.
- Pass each page's validated department through verify, mint, and healer operations instead of using a magic `all` scope.
- Apply database changes through idempotent, versioned migrations. Use separate non-superuser query and ingestion roles.
- Publish host-sync output from a dedicated replica/snapshot area and make basic-memory read that replica, not `./wiki` from a writable repository mount.
- Remove pseudo-vector fallback entirely. Tests inject fake embedders; production failures roll back, retry a bounded number of times, then fail visibly.
- Centralize wiki validation in `scout/vault.py`; `compile_note.py` renders and validates with that same contract before atomic writes.

## Acceptance criteria

- The exposed credential is revoked, the tracked value is redacted, and tracked/history scans have an explicit disposition.
- Missing/invalid identity, unauthorized narrowing, and pooled RLS scope reuse cannot return protected rows.
- Fresh and existing PostgreSQL volumes both receive the same roles and RLS policies.
- Host-sync tests prove the developer working tree is byte-for-byte/status unchanged.
- Embedding or initial-sync failures never create fake data and prevent false readiness.
- Compiler path traversal, arbitrary-file cloud upload, invalid departments, failed minting, and partial page/index writes are rejected.
- CI distinguishes verifier exit codes 0/1/2 and verifies again after every heal path.
- Offline, integration, lint, type, vault, secret, Compose, health, MCP, RLS, and address gates all pass before a PR is proposed.

## Main risks

- Auth changes intentionally break unauthenticated clients; migration documentation and local-only development configuration are required.
- RLS migrations can lock out Scout if role or scope wiring is wrong; fresh/upgrade tests must precede deployment.
- History rewriting disrupts collaborators and cannot be bundled into ordinary branch work.
- Stricter wiki validation exposes existing invalid `security`/`sre` departments and navigational page section drift; those pages need a reviewed mechanical migration.
- Documentation includes active guidance, historical proposals, generated wiki knowledge, and immutable raw evidence; each class needs a different treatment.
