# Implementation Plan: Agent Package Enhancements (`packages/snp-agent`) (V5)

### Goal

Transform `packages/snp-agent/` into a production-grade, standardized, fully distributable AI Agent Package. Implement:
1. Standard Agent Manifests (`manifest.json`, `package.json`).
2. Automated Bidirectional Package Sync & Integrity Test Suite (`tests/test_agent_package_sync.py`).
3. Concrete Few-Shot Tool Input/Output Examples & RBAC Clearance Guidance in all `SKILL.md` files.
4. Single-Command Installer, Bundler, and Synchronization CLI (`scripts/export_agent_bundle.py`).

---

### Assumptions

1. **Package Scope**: `packages/snp-agent/` is the canonical distribution source for all agent rules, instructions, skills, and workflows.
2. **Local Environment Mirroring**: The active workspace uses `.agent/` for in-repo Superpowers/Antigravity executions; `.agent/` and `packages/snp-agent/` must maintain 100% parity.
3. **Multi-IDE Compatibility**: The package targets Cursor, Claude Code, Gemini CLI, Antigravity, VS Code, and Windsurf without hardcoding machine-specific file paths.
4. **Offline Test Policy**: All package validation, bundling, and synchronization tests run strictly offline with `--disable-socket`.

---

### Plan

#### Phase 1: Standard Agent Package Manifests

1. **Create Package Manifests (`manifest.json` & `package.json`)** (2–5 min)
   - Files: `packages/snp-agent/manifest.json`, `packages/snp-agent/package.json`, `.agent/manifest.json`
   - Change: Define package metadata (name `@snp/memory-agent`, version `2.0.0`, description, author, license), required MCP servers (`basic-memory` on `:8765`, `scout` on `:8080`), required tools (`search_notes`, `read_note`, `write_note`, `rag_fetch`), platform targets, and directory entrypoints (`rules/`, `instructions/`, `skills/`, `workflows/`).
   - Verify: `python3 -c "import json; json.load(open('packages/snp-agent/manifest.json'))"`.

---

#### Phase 2: Package Parity & Integrity Test Suite

2. **Implement Package Sync & Frontmatter Validator Unit Tests** (5–10 min)
   - Files: `tests/test_agent_package_sync.py`
   - Change: Add unit test suite that:
     - Asserts 100% file tree and content parity between `packages/snp-agent/` and `.agent/`.
     - Validates all `SKILL.md` frontmatter schemas (`name`, `description`).
     - Validates all `workflows/*.md` slash-command descriptions.
     - Validates `manifest.json` schema and references.
   - Verify: `uv run pytest tests/test_agent_package_sync.py --disable-socket -v`.

---

#### Phase 3: Few-Shot Tool Examples & RBAC Clearance Scoping in Skills

3. **Enrich Retrieval & Compilation Skills with Tool I/O Payloads** (5–10 min)
   - Files:
     - `packages/snp-agent/skills/snp-rag-fetch/SKILL.md` & `.agent/skills/snp-rag-fetch/SKILL.md`
     - `packages/snp-agent/skills/snp-search-wiki/SKILL.md` & `.agent/skills/snp-search-wiki/SKILL.md`
     - `packages/snp-agent/skills/snp-compile-wiki/SKILL.md` & `.agent/skills/snp-compile-wiki/SKILL.md`
   - Change: Add exact JSON-RPC input/output blocks for `rag_fetch`, `search_notes`, `read_note`, `write_note`, and `mint.py` invocations. Include error handling for `no_source`, HTTP 401/403, and locator specifications.
   - Verify: View files and check markdown syntax.

4. **Enrich System Lifecycle & Operations Skills** (5–10 min)
   - Files:
     - `packages/snp-agent/skills/snp-auto-heal-vault/SKILL.md` & `.agent/skills/snp-auto-heal-vault/SKILL.md`
     - `packages/snp-agent/skills/snp-verify-vault/SKILL.md` & `.agent/skills/snp-verify-vault/SKILL.md`
     - `packages/snp-agent/skills/snp-ingest-raw-data/SKILL.md` & `.agent/skills/snp-ingest-raw-data/SKILL.md`
     - `packages/snp-agent/skills/snp-export-mcp/SKILL.md` & `.agent/skills/snp-export-mcp/SKILL.md`
     - `packages/snp-agent/skills/snp-bootstrap-system/SKILL.md` & `.agent/skills/snp-bootstrap-system/SKILL.md`
   - Change: Add exit code semantics (0=PASS, 1=DRIFT/FAIL, 2=INFRA), CLI command triggers, and IDE configuration snippets.
   - Verify: View files and check consistency.

5. **Enrich Query Protocol with RBAC Clearance Scoping Guidelines** (5–10 min)
   - Files: `packages/snp-agent/instructions/query_protocol.instructions.md`, `.agent/instructions/query_protocol.instructions.md`
   - Change: Detail caller clearance scope resolution (`redteam`, `blueteam`, `ai_eng`, `infra`), explain how to pass `--department`, and enforce rule that callers can narrow but never expand clearance to `all`.
   - Verify: `git diff packages/snp-agent/instructions/query_protocol.instructions.md`.

---

#### Phase 4: Single-Command Installer, Bundler & Sync CLI

6. **Implement `scripts/export_agent_bundle.py`** (5–10 min)
   - Files: `scripts/export_agent_bundle.py`
   - Change: Create a robust CLI utility supporting:
     - `--bundle`: Generates a portable `.tar.gz` distribution package in `dist/`.
     - `--sync`: Synchronizes `packages/snp-agent/` <-> `.agent/` bidirectionally with `--direction packages-to-agent` or `agent-to-packages`.
     - `--install <target>`: Deploys agent configuration into target environment (Cursor, Claude Code, Gemini CLI, Antigravity, VS Code).
     - `--verify`: Runs manifest and structure verification.
   - Verify: `uv run python scripts/export_agent_bundle.py --verify`.

7. **Add Unit Tests for Installer & Bundler** (5–10 min)
   - Files: `tests/test_export_agent_bundle.py`
   - Change: Test bundle archive creation, extraction, install target file generation, sync logic, and error handling in isolated temporary directories.
   - Verify: `uv run pytest tests/test_export_agent_bundle.py --disable-socket -v`.

---

#### Phase 5: Consolidated Quality Gates & Verification

8. **Run Full Test & Quality Suite** (5–10 min)
   - Files: Entire repository
   - Commands:
     - `uv run pytest tests/test_agent_package_sync.py tests/test_export_agent_bundle.py -v`
     - `timeout 300s uv run pytest -m 'not integration' --disable-socket -q`
     - `uv run ruff check .`
     - `uv run mypy scout scripts tests`
     - `uv run python scripts/export_agent_bundle.py --bundle`
     - `uv run python scripts/scan_secrets.py --worktree --untracked`
   - Verify: All tests pass, linter exits 0, mypy exits 0, bundle artifact generated in `dist/`.

---

### Risks & Mitigations

1. **Parity Drift Between `.agent/` and `packages/snp-agent/`**:
   - *Risk*: Modifying `.agent/` during local work while forgetting `packages/snp-agent/` causes distribution drift.
   - *Mitigation*: Enforce automated parity checks in `tests/test_agent_package_sync.py` and provide one-command `python scripts/export_agent_bundle.py --sync`.
2. **Client Environment Path Differences**:
   - *Risk*: Hardcoding `/home/ple/...` in generated client configs breaks other developers' environments.
   - *Mitigation*: Use relative paths, environment variable expansion (`${env:VAR}` / `${VAR}`) and standard home directory resolution (`~`).

---

### Rollback Plan

- All changes are authored on branch `fix/architecture-security-hardening`.
- If packaging files cause conflicts, revert using `git checkout HEAD -- packages/snp-agent/ .agent/ scripts/export_agent_bundle.py`.
- No database tables or runtime Docker containers are modified by this packaging enhancement.
