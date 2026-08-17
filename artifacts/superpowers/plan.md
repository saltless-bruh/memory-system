# Implementation Plan: Dependency Hardening, CI/CD Security Audit Gate & Routine Patch Upgrades

### Goal
Implement the 3 recommended actions from the dependency audit:
1. **Pinning Rationale & Lockfile Preservation**: Document and maintain the `fastmcp==3.3.1` pinning contract for MCP Streamable HTTP stability.
2. **Automated Vulnerability Scan Gate in CI/CD**: Integrate `pip-audit` automated scanning into `.gitea/workflows/auto-healer.yaml` for both PR verification and weekly scheduled sweeps.
3. **Routine Patch Upgrades & Regression Verification**: Upgrade non-breaking tool/runtime patches (`uvicorn`, `mypy`, `ruff`), sync `uv.lock`, and verify 100% test and lint pass rates.

---

### Assumptions
- Python virtual environment is managed via `uv` on Python 3.12+.
- `pip-audit` runs seamlessly via `uv run --with pip-audit pip-audit` without bloating runtime production container images.
- Gitea Actions workflow runner inherits the `uv` toolchain.
- `fastmcp==3.3.1` remains pinned to preserve existing MCP client/server streamable-http protocol compatibility across `basic-memory` and `scout`.

---

### Plan

1. **Document Version Pinning in Manifests**
   - Files: `pyproject.toml`, `scout/requirements.txt`
   - Change:
     - Add explicit comments in `pyproject.toml` and `scout/requirements.txt` detailing the architectural reason for pinning `fastmcp==3.3.1` (Streamable HTTP MCP protocol stability and deterministic `{status, context, citations}` output schema).
   - Verify: `git diff pyproject.toml scout/requirements.txt`

2. **Automate Vulnerability Scanning Gate in CI/CD**
   - Files: `.gitea/workflows/auto-healer.yaml`
   - Change:
     - Add a `Security audit (pip-audit)` step in `pr-heal` job immediately after `Install uv and dependencies`.
     - Add a `Security audit (pip-audit)` step in `scheduled-sweep` job.
   - Verify: Run `uv run --with pip-audit pip-audit` locally to ensure exit code 0.

3. **Execute Safe Patch Upgrades for Development & Server Tooling**
   - Files: `uv.lock`, `pyproject.toml`
   - Change:
     - Upgrade patch releases: `uvicorn` (`0.52.1` $\rightarrow$ `0.52.3`), `mypy` (`2.3.0` $\rightarrow$ `2.3.1`), `ruff` (`0.16.1` $\rightarrow$ `0.16.3`).
     - Run `uv lock --upgrade-package uvicorn --upgrade-package mypy --upgrade-package ruff` and `uv sync`.
   - Verify: `uv tree` and `uv pip list | grep -E "uvicorn|mypy|ruff"`

4. **Full Regression & Security Gate Verification**
   - Files: N/A (Verification Phase)
   - Change:
     - Run `uv run --with pip-audit pip-audit` (Verify 0 vulnerabilities).
     - Run `uv run ruff check .` (Verify 0 lint errors).
     - Run `python3 scripts/gen_index.py --check` (Verify 0 index/frontmatter errors).
     - Run `uv run python scripts/verify_addresses.py` (Verify 19/19 addresses PASS).
     - Run `uv run pytest` (Verify 170/170 tests PASS).
   - Verify: All commands return exit code 0.

---

### Risks & Mitigations
- **Risk**: Patch upgrades in `ruff` or `mypy` introduce new strict lint/type rules.
  - **Mitigation**: Run `ruff check .` and `mypy` immediately; our code already follows strict typing (`disallow_untyped_defs = true`).
- **Risk**: CI workflow fails if `pip-audit` cannot reach PyPI advisory database.
  - **Mitigation**: `uv run --with pip-audit pip-audit` uses cached database with fallback timeouts.

---

### Rollback Plan
- If any patch upgrade introduces unexpected regressions or breaks any of the 170 pytest tests, revert `uv.lock` and `pyproject.toml` using `git checkout main -- uv.lock pyproject.toml && uv sync`.
