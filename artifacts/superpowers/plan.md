# Implementation Plan: Comprehensive Architecture & Security Hardening (9 Audit Findings)

---

### Goal
Resolve all 9 technical and security audit findings raised by Codex to ensure strict fail-closed security, eliminate silent failures, enforce mechanical contracts, protect local workspace integrity, and align all documentation and CLI ergonomics with the production V2 architecture.

---

### Assumptions
1. PostgreSQL 16 + pgvector and LiteLLM proxy are running in local Docker environment.
2. Changes must preserve 100% backward compatibility for existing valid `wiki/*.md` pages and `raw/` files.
3. Root `.agent/` and `packages/snp-agent/` must remain synchronized where domain rules/instructions/skills are updated.

---

### Plan

1. **Step 1: Harden Scout MCP Scope & Dynamic Department Authentication**
   - Files: `scout/mcp_server.py`, `tests/test_mcp_server.py`
   - Change: Add optional `department: str | list[str] | None = None` parameter to `rag_fetch_tool`; dynamically construct `Scope(roles=frozenset(depts))` from parameter or `SCOUT_DEFAULT_DEPTS` env (defaulting to `{"all"}` in solo dev mode).
   - Verify: `uv run pytest tests/test_mcp_server.py`

2. **Step 2: Fail-Fast Embeddings & Error Handling (Eliminate Silent Fake Vectors)**
   - Files: `scout/chunker.py`, `tests/test_pgvector_backend.py`, `tests/test_sync_job.py`
   - Change: Define `EmbeddingError` exception and raise when LiteLLM/OpenAI fails in production; allow fake pseudo-vectors only when `allow_mock=True` or `SNP_ALLOW_MOCK_EMBEDDINGS=true` is explicitly set in testing.
   - Verify: `uv run pytest tests/test_pgvector_backend.py tests/test_sync_job.py`

3. **Step 3: Startup Cold-Start Synchronization in Sync-Job**
   - Files: `scout/sync_job.py`, `tests/test_sync_job.py`
   - Change: Call `await sync_once(indexer)` on startup inside `watch()` before entering the `awatch` filesystem loop to guarantee pre-existing `raw/` files are indexed on cold start.
   - Verify: `uv run pytest tests/test_sync_job.py`

4. **Step 4: Fix Scout-DIY Real Vault Seam & Import**
   - Files: `scout/diy_engine.py`, `tests/test_diy_engine.py`
   - Change: Replace deleted `spikes._lib.vault` dynamic import in `ScoutDiyEngine.from_vault` with `from scout import vault`; add direct test against real `wiki/` directory without mocks.
   - Verify: `uv run pytest tests/test_diy_engine.py`

5. **Step 5: Scope Host-Sync to `wiki/` Subtree & Restrict Safe Directory**
   - Files: `scripts/host_sync.py`, `docker-compose.yml`, `tests/test_host_sync.py`
   - Change: Replace repo-wide `git reset --hard` with `git fetch origin main && git checkout origin/main -- wiki/`; remove `safe.directory *` wildcard and restrict strictly to `VAULT_DIR`.
   - Verify: `uv run pytest tests/test_host_sync.py`

6. **Step 6: Database Security, RLS on `rag_documents` & Localhost Port Binding**
   - Files: `config/postgres/init.sql`, `docker-compose.yml`
   - Change: Enable and force RLS on `rag_documents` with department overlap policy; bind PostgreSQL host port to `127.0.0.1:5432:5432` in `docker-compose.yml`.
   - Verify: `docker compose config` & inspect SQL policies.

7. **Step 7: Enforce Authoring Contracts & Pluralization in `scripts/compile_note.py`**
   - Files: `scripts/compile_note.py`, `tests/test_compile_note.py`
   - Change: Fix directory mapping (`entity` $\rightarrow$ `entities`, `technique` $\rightarrow$ `techniques`, etc.); abort compilation if `mint_address` returns `FAIL` or `DRIFT` (requiring valid minted addresses per Rule R-6.3); add `--dept` CLI argument.
   - Verify: `uv run pytest tests/test_compile_note.py`

8. **Step 8: Close CI Auto-Healer Verification Loop**
   - Files: `.gitea/workflows/auto-healer.yaml`
   - Change: Add `uv run python scripts/verify_addresses.py` verification gate immediately following `scout/healer.py --ci` so unsuccessful heals fail the CI job.
   - Verify: Check YAML syntax and step ordering in `.gitea/workflows/auto-healer.yaml`.

9. **Step 9: CLI Argument Ergonomics & Documentation Drift Clean-up**
   - Files: `scripts/ingest_v2.py`, `scripts/export_mcp_config.py`, `README.md`, `docs/CONNECT_AGENTS.md`, `packages/snp-agent/instructions/agent_guide.instructions.md`
   - Change: Support `--dept` as an alias for `--allowed-depts` in `ingest_v2.py`; add default interactive/all-export mode in `export_mcp_config.py`; replace "bi-temporal" marketing jargon with "dual-layer Git + pgvector architecture"; remove DOCX/Ollama legacy claims.
   - Verify: `python3 scripts/export_mcp_config.py --print` and `python3 scripts/ingest_v2.py --help`.

10. **Step 10: Full Regression Gate & Container Rebuild**
    - Files: All project files
    - Change: Run the entire test suite, linter, strict type checker, and rebuild containers.
    - Verify: `uv run pytest && uv run ruff check . && uv run mypy scout scripts && python3 scripts/gen_index.py --check && docker compose up -d --build`

---

### Risks & Mitigations
- **Risk 1: Breaking offline unit tests by removing fake embeddings fallback**:
  - *Mitigation*: Provide explicit `allow_mock=True` parameter on `LiteLLMEmbedder` or check `SNP_ALLOW_MOCK_EMBEDDINGS=1` in test fixtures so unit tests remain fast and network-isolated while production fails fast.
- **Risk 2: Breaking existing MCP clients by adding `department` parameter**:
  - *Mitigation*: Make `department` optional with sensible defaults (`DEFAULT_DEPTS` or `all`), maintaining 100% backward compatibility for single-user and multi-user environments.
- **Risk 3: `git checkout origin/main -- wiki/` behavior when local changes exist in `wiki/`**:
  - *Mitigation*: Webhook sync is designated for the read-only wiki replica where Gitea is the single source of truth; scoping checkout strictly to `wiki/` protects developer working trees from being wiped.

---

### Rollback Plan
All modifications are managed via Git on branch `main`. If any step introduces regressions, rollback using `git revert` or checkout of previous commit `a4fd9ce`.
