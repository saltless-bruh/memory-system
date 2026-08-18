# Implementation Plan: Comprehensive Architecture & Security Hardening (V2 Revised)

---

### Goal
Remediate all 9 technical and security audit findings plus the credential incident response across the codebase. Implement server-side fail-closed authorization, database-level RLS on all tables, isolated host-sync worktrees, fail-fast embedding pipelines, complete authoring contract validation, exit-code-aware CI verification, clean test topology, and full documentation alignment on a dedicated PR feature branch.

---

### Assumptions & Boundaries
1. **Branch & PR-First**: All work is performed on feature branch `fix/architecture-security-hardening` and merged via Pull Request.
2. **Explicit Compatibility Boundaries**:
   - MCP `rag_fetch`: Caller-provided `department` parameter may only *narrow* the server-side authorized scope, never broaden it.
   - Solo Dev Mode: Insecure `{"all"}` scope is only permitted when explicitly enabled via `SCOUT_AUTH_MODE=development` (defaults to fail-closed `authenticated` mode in production).
3. **Test Topology**: Unit tests remain 100% offline and fast (dependency-injected mocks); live service dependencies are strictly segregated under `@pytest.mark.integration`.

---

### Finding-to-Step Traceability Matrix

| Audit Finding / Area | Remediated in Step(s) | Key Deliverable |
|---|---|---|
| **#1: Credential Incident Response** | Step 1 | Redacted working tree, history rewrite verified, secret scan gate. |
| **#2: Department Isolation Auth Bypass** | Step 2 | Server-side trusted token/env identity; caller argument can only narrow scope; denial tests. |
| **#3: Database Hardening & Exposure** | Step 3 | Dynamic `rag_app_role` password; RLS forced on `rag_documents` + `rag_chunks`; localhost bind. |
| **#4: Host-Sync Blast Radius** | Step 4 | Isolated clone/worktree sync; no `safe.directory *`; preserves developer working tree. |
| **#5: Silent Fake Vector Fallback** | Step 5 | Fail-fast `EmbeddingError` in production; explicit DI `allow_mock=False` by default. |
| **#6: `compile_note.py` Contract Violations** | Step 6 | `entities` folder mapping; strict mint verification; 7-field check; PR branch proposal. |
| **#7: CI Healer Verification Swallow** | Step 7 | Post-heal `verify_addresses.py` gate; distinction between drift and infra failure. |
| **#8: Sync-Job Cold-Start Gap** | Step 8 | `initial_sync=True` parameter in `main()` before watcher loop; cold-start test. |
| **#9: Scout-DIY Dead Import** | Step 9 | Switched to `scout.vault`; isolated temporary vault integration test; FTS rebuild test. |
| **#10: Documentation & CLI Drift** | Step 10 | `--dept` alias; `export_mcp_config` CLI modes; purged DOCX, 3072-dim & "bi-temporal" claims. |
| **Verification & Quality Gate** | Step 11 | Secret scan, offline unit gate, integration test gate, index check, docker config. |

---

### Plan

1. **Step 1: Formalize Credential Incident Response & Secret Scanning**
   - Files: `tests/test_secrets_hygiene.py`, `artifacts/multimodal_system_analysis.md`
   - Change: Verify working tree sanitization and historical purge; add automated test in `tests/test_secrets_hygiene.py` that scans tracked files and commit messages against known credential patterns and blocks future commits with hardcoded secrets.
   - Verify: `uv run pytest tests/test_secrets_hygiene.py`

2. **Step 2: Server-Side Identity & Fail-Closed Department Authorization**
   - Files: `scout/mcp_server.py`, `scout/types.py`, `tests/test_mcp_server.py`
   - Change:
     - Add `SCOUT_AUTH_MODE` (values: `development` | `authenticated`, default: `authenticated`).
     - In `authenticated` mode, derive base authorized departments from server-side bearer token claims or `SCOUT_ALLOWED_DEPTS` server environment.
     - Allow caller `department` parameter only to *narrow* (intersect with) the authorized set. If unauthorized department is requested or token is invalid, return fail-closed empty/denial response.
     - Add unit tests verifying: unauthorized department denial, narrowing valid scope, development mode fallback, and invalid token rejection.
   - Verify: `uv run pytest tests/test_mcp_server.py`

3. **Step 3: Database Security, RLS on `rag_documents` & Localhost Port Binding**
   - Files: `config/postgres/init.sql`, `docker-compose.yml`, `tests/test_postgres_rls.py`
   - Change:
     - Enable & force RLS on `rag_documents` table with `dept_overlap_policy`.
     - In `config/postgres/init.sql`, parameterize application role creation and grant restricted SELECT permissions to `rag_app_role`.
     - Bind PostgreSQL host port to `127.0.0.1:5432:5432` in `docker-compose.yml`.
     - Add integration test connecting as `rag_app_role` testing that queries without `scout.current_depts` return 0 rows for both `rag_documents` and `rag_chunks`.
   - Verify: `uv run pytest -m integration tests/test_postgres_rls.py`

4. **Step 4: Safe Host-Sync Isolation & Worktree Protection**
   - Files: `scripts/host_sync.py`, `docker-compose.yml`, `tests/test_host_sync.py`
   - Change:
     - Remove `git config --global --add safe.directory "*"` and restrict strictly to `VAULT_DIR`.
     - Refactor `_perform_git_sync` to operate on a dedicated isolated worktree/path or use `git fetch origin $GIT_BRANCH && git checkout origin/$GIT_BRANCH -- wiki/` without running destructive `git reset --hard` on the entire repo root.
     - Add tests verifying modified, untracked, and deleted files outside `wiki/` are untouched after webhook sync.
   - Verify: `uv run pytest tests/test_host_sync.py`

5. **Step 5: Fail-Fast Embeddings & Explicit Dependency Injection**
   - Files: `scout/chunker.py`, `scout/backends/pgvector.py`, `tests/test_chunker.py`, `tests/test_pgvector_backend.py`
   - Change:
     - Define `EmbeddingError(RuntimeError)`.
     - In `LiteLLMEmbedder.embed_texts()`, set `allow_mock=False` by default. If LiteLLM / API request fails, raise `EmbeddingError` immediately.
     - In `scout/backends/pgvector.py` and `sync_job.py`, let `EmbeddingError` bubble up so failed batches abort and are quarantined rather than indexed with pseudo-vectors.
     - Provide mock embedders via dependency injection in test fixtures.
   - Verify: `uv run pytest tests/test_chunker.py tests/test_pgvector_backend.py`

6. **Step 6: Enforce Complete Authoring Contract & Pluralization in `scripts/compile_note.py`**
   - Files: `scripts/compile_note.py`, `tests/test_compile_note.py`
   - Change:
     - Fix category directory mapping: `{"entity": "entities", "technique": "techniques", "concept": "concepts", "playbook": "playbooks"}`.
     - Validate `--dept` against allowed department vocabulary (`redteam`, `blueteam`, `ai_eng`, `infra`, `general`).
     - Fail fast if `mint_address` returns `FAIL` or `DRIFT`; do not generate partial or unverified frontmatter.
     - Validate that summary is exactly one sentence, all 7 frontmatter fields are present, and body sections are ordered per contract.
     - Add `--branch` / PR proposal flag using `scripts.propose_page`.
   - Verify: `uv run pytest tests/test_compile_note.py`

7. **Step 7: Exit-Code-Aware CI Auto-Healer Verification Loop**
   - Files: `.gitea/workflows/auto-healer.yaml`
   - Change:
     - Differentiate between address drift and infrastructure failure (preserving exit codes).
     - Add unconditional post-heal verification step: `uv run python scripts/verify_addresses.py`.
     - Ensure any post-heal verification failure aborts the workflow with non-zero exit code.
   - Verify: Syntax and step-ordering audit of `.gitea/workflows/auto-healer.yaml`.

8. **Step 8: Entrypoint Startup Cold-Start Synchronization in Sync-Job**
   - Files: `scout/sync_job.py`, `tests/test_sync_job.py`
   - Change:
     - Add explicit `initial_sync: bool = False` parameter to `watch()`.
     - In `main()` entrypoint, run `await sync_once(indexer)` before starting `watch(indexer, initial_sync=False)`.
     - Keep test watcher loops predictable while guaranteeing production cold start indexes pre-existing files.
   - Verify: `uv run pytest tests/test_sync_job.py`

9. **Step 9: Scout-DIY Vault Seam, Isolated Vault Test & FTS Rebuild**
   - Files: `scout/diy_engine.py`, `tests/test_diy_engine.py`
   - Change:
     - Replace dynamic `spikes._lib.vault` import with `from scout import vault`.
     - Add integration test running `ScoutDiyEngine.from_vault()` against a temporary synthetic vault with real frontmatter and bodies.
     - Add test verifying SQLite FTS index rebuild when vector cache exists but `search.db` is missing.
   - Verify: `uv run pytest tests/test_diy_engine.py`

10. **Step 10: CLI Ergonomics & Comprehensive Documentation Inventory Update**
    - Files: `scripts/ingest_v2.py`, `scripts/export_mcp_config.py`, `README.md`, `AGENTS.md`, `docs/CONNECT_AGENTS.md`, `packages/snp-agent/instructions/agent_guide.instructions.md`, `.agent/instructions/agent_guide.instructions.md`
    - Change:
      - In `scripts/ingest_v2.py`: Support `--dept` as an alias for `--allowed-depts`.
      - In `scripts/export_mcp_config.py`: Add `--all` option, interactive prompt when `--client` is omitted in TTY, and clear error in non-interactive mode. Add CLI unit tests.
      - In `README.md` and documentation: Replace "bi-temporal" with "dual-tier Git + pgvector architecture"; remove all legacy DOCX, 3072-dimension, and local Ollama claims; document supported formats accurately.
    - Verify: `python3 scripts/export_mcp_config.py --print --client gemini` and `uv run pytest tests/test_export_mcp_config.py`.

11. **Step 11: Full Regression Gate & Pull Request Proposal**
    - Files: None (Verification and PR proposal)
    - Change:
      - Run offline test gate: `uv run pytest -m "not integration"`
      - Run integration test gate: `uv run pytest -m "integration"`
      - Run strict linter and type checker: `uv run ruff check . && uv run mypy scout scripts`
      - Run vault linter: `python3 scripts/gen_index.py --check && uv run python scripts/verify_addresses.py`
      - Validate Docker compose syntax: `docker compose config --quiet`
      - Open Pull Request from branch `fix/architecture-security-hardening` to `main`.
    - Verify: All automated gates return exit code 0.

---

### Risks & Mitigations
- **Risk 1: Client breakage when switching to authenticated department mode**:
  - *Mitigation*: Support `SCOUT_AUTH_MODE=development` for local standalone usage, while defaulting to fail-closed token validation in enterprise deployments.
- **Risk 2: CI environment missing PostgreSQL for integration tests**:
  - *Mitigation*: Use pytest markers (`@pytest.mark.integration`) so unit tests run completely offline with fake backends, while integration tests run when database services are present.
- **Risk 3: Host-sync git checkout conflicts with local uncommitted edits**:
  - *Mitigation*: Scope `git checkout origin/$GIT_BRANCH -- wiki/` strictly to the `wiki/` directory and test on modified/untracked files outside the wiki.

---

### Rollback Plan
All changes will be developed on feature branch `fix/architecture-security-hardening`. If any phase fails verification, atomic per-step commits can be reverted using `git revert <commit_sha>` or the feature branch discarded without impacting `main`.
