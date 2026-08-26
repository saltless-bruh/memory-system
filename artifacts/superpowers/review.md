# Release-candidate re-review — v0.2.1

Date: 2026-08-26
Scope: complete pre-commit candidate after correcting the initial release review
findings and applying the approved staging/backup practices.

## Blockers

None found in the repository candidate.

The two previous blockers are resolved:

1. Manifest, release preflight, and stack preflight now require or propagate an
   explicit Compose project. They preserve the ordered Compose overlay files in
   both observed evidence and failure remedies; staging therefore does not fall
   back to the live default configuration.
2. Scout, basic-memory, and host-sync now all receive `SNP_GIT_REVISION`, carry
   the same OCI revision label, and use explicit overridable local image names.
   The supply-chain tests pin this contract.

## Majors

None found.

## Minors

1. The current wiki index reports two orphan-page warnings. They remain warnings
   under the repository contract and do not invalidate source links or the
   generated index, but should be cross-linked before a larger corpus release.

2. The custom archive/restore workflow is unit-tested but has not yet been run
   against the actual staging PostgreSQL instance. This is an intentional next
   release gate, not a completed claim: the candidate must still be built,
   restored, and preflighted in `snp-v021-staging` before it can be promoted.

## Nits

- Release evidence and temporary test logs belong outside the repository. The
  new tools enforce this for manifest, backup, and restore-result outputs.

## Verification reviewed

- `timeout 300s uv run pytest -m 'not integration' --disable-socket -q` —
  **1446 passed, 29 deselected**, exit 0 (151.13 seconds).
- `uv run ruff check .` — pass.
- `uv run ruff format --check .` — **356 files already formatted**.
- `uv run mypy scout scripts` — **77 source files, success**.
- `uv run python scripts/gen_index.py --check` — 7 pages, 0 errors, 2 orphan
  warnings, index current.
- `uv run python scripts/scan_secrets.py --worktree --untracked` — pass.
- `git diff --check` — pass.
- Base and explicit staging Compose configurations — pass.
- Focused staging/backup/manifest/preflight/supply-chain tests — pass.

## Overall summary and next actions

**Approved for the clean v0.2.1 candidate commit and annotated tag.** This is
not yet a production-promotion approval: after the tag, build only the tagged
candidate in `snp-v021-staging`, take and restore the verified PostgreSQL
archive, record the container capability/manifest, then require release
preflight to pass before any controlled ingest or production transition.
