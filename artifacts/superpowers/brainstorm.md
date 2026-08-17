# Brainstorm: Codebase Cleanup & Refactoring

## Goal
Clean up the codebase, remove dead/stale artifacts, restore SNP domain skills alongside Superpowers, resolve linting and formatting errors across all Python files, and ensure 100% pass rate on vault integrity checks (`gen_index.py --check`) and test suites (`pytest`).

## Constraints
- **Zero regression**: All 141 pytest unit and integration tests must continue to pass.
- **Contract preservation**: Frontmatter contracts, address minting mechanics, and RAG bridge interfaces must remain intact.
- **Clean Git working tree**: Address untracked temporary files and restore deleted `snp-*` domain skills.
- **Ruff compliance**: Code formatting (line-length: 88) and lint rules (E, F, I, UP, B, SIM, ASYNC) should pass cleanly.

## Known Context
- `git status` shows 8 deleted `snp-*` skills in `.agent/skills/` because Superpowers was bootstrapped into `.agent`. The originals are backed up in `.agent-snp/skills/`.
- 3 untracked wiki notes in `wiki/concepts/` (`litellm-dashboard-image.md`, `query-results-data.md`, `query-wiki-script.md`) reference non-existent files under `raw/`, causing `python3 scripts/gen_index.py --check` to fail.
- `Untitled.md` in root is an empty 0-byte scratch file.
- `ruff check .` reported 72 formatting and style errors across evaluation and test scripts.
- Python environment dependency synchronization (`uv sync --all-extras`) is needed for test execution.

## Risks
1. **Accidental Deletion of Test Fixtures**: Moving or deleting untracked wiki concepts could break tests if any test relied on them (verified: no tests rely on those 3 untracked files).
2. **Skill Collision**: Restoring `snp-*` skills into `.agent/skills/` alongside `superpowers-*` must preserve valid YAML frontmatter and unique names (verified: names are completely distinct).
3. **Format Regressions**: Running `ruff format` on complex docstrings or test fixtures could alter multi-line JSON strings (mitigated by running `uv run pytest` immediately after formatting).

## Options

### Option 1: Minimal Cleanup
- Only fix the 3 failing wiki notes and delete `Untitled.md`.
- *Pros*: Quickest.
- *Cons*: Leaves deleted `snp-*` skills unstaged, leaves 72 ruff lint errors, leaves untracked backups messy.

### Option 2: Full Systematic Cleanup & Stabilization (Recommended)
- **Step 1: Skill Coexistence**: Restore `snp-*` skills from `.agent-snp/skills/` to `.agent/skills/` so both Superpowers and SNP domain skills coexist, and remove `.agent-snp` temporary backup.
- **Step 2: Vault Hygiene**: Remove 0-byte `Untitled.md` and remove the 3 unbacked test notes from `wiki/concepts/` (or create matching sample dummy fixtures in `raw/` if intended, but since they were test scratch files, removing them cleans the vault).
- **Step 3: Vault Index Synchronization**: Run `python3 scripts/gen_index.py` to deterministically regenerate `wiki/index.md` and verify with `--check`.
- **Step 4: Python Code Formatting & Lint Fixes**: Run `uv run ruff format .` and `uv run ruff check --fix .`, then manually resolve any remaining line-length or import errors in `tests/eval_*.py` and `scripts/*.py`.
- **Step 5: Full Verification**: Run `uv run pytest`, `python3 scripts/gen_index.py --check`, and `uv run ruff check .`.
- *Pros*: Completely clean working directory, zero lint errors, 100% test pass rate, perfect preparation for Phase 1.1 hotfix and Phase 2 pgvector migration.
- *Cons*: Touches several test/eval files for line-length formatting.

## Recommendation
Execute Option 2 (Full Systematic Cleanup & Stabilization).

## Acceptance Criteria
- [ ] Both Superpowers skills and `snp-*` domain skills coexist in `.agent/skills/`.
- [ ] No temporary 0-byte or broken files in root or `wiki/`.
- [ ] `python3 scripts/gen_index.py --check` passes with 0 errors and 0 warnings (or clean status).
- [ ] `uv run ruff check .` passes with 0 errors.
- [ ] `uv run pytest` passes 100% (all 141 tests passing).
