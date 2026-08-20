# Session Log: Architecture & Security Hardening Remediation

**Date:** 2026-08-18  
**Repository:** `saltless-bruh/memory-system` (`snp-memory-system-main`)  
**Branch:** `fix/architecture-security-hardening`  
**Base Commit:** `92f5b42ba1f1cd5f9b10b9f010078b9b73cf5dcd`  
**Artifact Path:** `artifacts/session_log_2026-08-18.md`

---

## 1. Executive Summary

This session focused on executing a comprehensive remediation of security blockers and architectural gaps across the SNP Memory System V2 stack. During the session, major work was completed across secrets hygiene, request-scoped authorization, PostgreSQL migration ledgers, fail-closed RLS policies, host-sync replica isolation, compiler containment, strict embedding dimension/cardinality validation, and test suite modernization.

Following this work, a secondary audit review (`artifacts/superpowers/review.md`) was received identifying 6 Blockers and 10 Majors regarding live HTTP transport auth integration, secret scanner token shape coverage, Compose volume mounts, ingest-role RLS write policies, CI gate 3-state execution, and test teardown determinism. A complete technical root-cause analysis and 4-phase remediation plan were formulated to address all findings.

---

## 2. Work Completed in Current Session

### A. Secret Hygiene & Zero-Exemption Scanner
- **`tests/test_secrets_hygiene.py`**: Refactored to test generic API key patterns using dynamically generated synthetic tokens with zero self-exclusions.
- **`scripts/scan_secrets.py`**: Implemented dual-mode secret scanner covering working tree files and historical Git commit diffs (`git log -p`) with binary-safe UTF-8 decoding.
- **`.gitleaks.toml`**: Configured repository Gitleaks rules for static secret detection.

### B. Request-Scoped Authorization Architecture
- **`scout/auth.py`**: Created authorization module implementing `AuthMode` (`JWT`, `STATIC`, `DEVELOPMENT`), `CallerIdentity`, canonical 4-department validation (`redteam`, `blueteam`, `ai_eng`, `infra`), and fail-closed scope narrowing.
- **`scout/mcp_server.py`**: Wired `scout.auth` into `rag_fetch_tool`, verifying caller credentials and ensuring caller-provided `department` arguments can only narrow authenticated department sets.
- **`tests/test_auth.py` & `tests/test_mcp_server.py`**: Added unit tests covering token verification, scope narrowing, and denial tests for unauthorized department access.

### C. Database Migrations & Fail-Closed RLS
- **`config/postgres/migrations/`**: Added `001_initial_schema.sql` (baseline DDL) and `002_rls_and_roles.sql` (application roles and fail-closed RLS policies).
- **`config/postgres/init.sql`**: Updated clean-boot database schema with unified fail-closed RLS on `rag_documents` and `rag_chunks`.
- **`scripts/migrate_postgres.py`**: Authored idempotent migration runner tracking applied versions in `schema_migrations`.
- **`tests/integration/test_postgres_rls.py`**: Added integration tests validating fail-closed behavior for unauthenticated queries and role-based department isolation.

### D. Host-Sync Dedicated Replica Isolation
- **`scripts/host_sync.py`**: Configured to operate strictly on isolated `VAULT_REPLICA_DIR` (`/vault-replica`), evaluate `GIT_BRANCH`, return HTTP 400 on malformed payloads, and return HTTP 503 on sync failures.
- **`tests/test_host_sync.py`**: Added tests for webhook signature validation, branch filtering, and non-wiki file preservation.

### E. Authoring Containment & Contract Validation
- **`scout/vault.py`**: Added validation for canonical department taxonomy, note types, single-sentence summaries, ISO dates, and ordered section headings (`TL;DR` -> `Technical Specifications` -> `Provenance` -> `Cross-References`).
- **`scripts/compile_note.py`**: Implemented strict containment checks ensuring inputs reside in `raw/` and outputs reside in `wiki/<category>/`, safe slug generation, overwrite protection (`--overwrite`), and mint verification abort.
- **Wiki Vault Fixes**: Updated non-canonical department tags across wiki notes (`sre` -> `infra`, `security` -> `blueteam`).

### F. Strict Embeddings & Cold-Start Sync Hardening
- **`scout/chunker.py`**: Enforced 1024-dimension check, batch cardinality checks, finite float validation (`math.isfinite`), removed silent mock fallback in production `LiteLLMBatchEmbedder`, and introduced deterministic `FakeEmbedder` for testing.
- **`scout/sync_job.py`**: Implemented cold-start initial sync fail-fast exit (`sys.exit(1)`) on initial synchronization failure.
- **`scout/diy_engine.py`**: Added FTS index deletion reconciliation for removed wiki pages.
- **`scout/ingest.py`**: Enforced `zip(strict=True)` across chunks and embeddings.

### G. Test Suite & Address Verification Alignment
- **`scripts/verify_addresses.py`**: Updated URL resolution to support `/v1/embeddings` and added `USE_FAKE_EMBEDDER` support for deterministic offline verification.
- **`tests/`**: Updated benchmark and ingest test suites to use `FakeEmbedder` and marked live database tests with `@pytest.mark.integration`.
- **Verification Results**:
  - `pytest`: 206/206 PASS (196 offline unit tests + 10 live integration tests).
  - `verify_addresses.py`: 19/19 addresses PASS (0 FAIL, 0 DRIFT).
  - `gen_index.py --check`: 11 pages PASS (0 errors, 0 warnings).
  - `ruff check .`: PASS (0 errors).

---

## 3. Secondary Audit Findings Analysis (`review.md`)

The independent review highlighted the following critical areas requiring further hardening:

```
+---------------------------------------------------------------------------------------------------+
| FINDING                            ROOT CAUSE / IMPACT                      REMEDIATION TARGET    |
+------------------------------------+----------------------------------------+---------------------+
| B1: FastMCP HTTP Request Auth &    FastMCP server lacks ASGI auth           Wrap FastMCP in       |
|     Incomplete JWT Claims          middleware; manual HMAC verification     FastAPI/ASGI handler; |
|                                    omitted exp, aud, iss, nbf, and alg.     PyJWT claim validation|
+------------------------------------+----------------------------------------+---------------------+
| B2: Scanner Token Format &         Regex required >24 suffix chars;         Regex: {20,} chars;   |
|     Untracked Files Blindspot      `scan_working_tree` scanned tracked      scan untracked files; |
|                                    files only (`git ls-files`).             full commit blob scan |
+------------------------------------+----------------------------------------+---------------------+
| B3: Host-Sync Compose Mount &      Compose mounts `./:/repo:rw`; host-sync  Dedicated named volume|
|     Developer Workspace Overwrite  operates directly on local repository.   `/vault-replica` +    |
|                                                                              atomic read snapshot  |
+------------------------------------+----------------------------------------+---------------------+
| B4: Ingest-Role RLS Policies &     Forced RLS tables had SELECT policy only Add FOR ALL policy for|
|     Superuser Runtime Fallback     `TO rag_app_role`; DML denied to ingest. `rag_ingest_role`;    |
|                                    Fallback used superuser.                 remove superuser env. |
+------------------------------------+----------------------------------------+---------------------+
| B5: CI Address Gate 3-State Logic  Gate treated infrastructure exit 2 as    Deterministic 0/1/2   |
|     & Department Propagation       drift; verifier dropped page departments. state machine; pass   |
|                                                                              page Scope to verifier|
+------------------------------------+----------------------------------------+---------------------+
| B6: Test Teardown Hangs, Types     Unclosed SQLite/HTTP connection handles; pytest-socket offline |
|     & Socket Prohibition           3 mypy errors; no socket isolation.      lockdown; clean close |
+------------------------------------+----------------------------------------+---------------------+
| M1-M10: Operational Architecture   Missing migration tests, embedding index Migration tests,      |
|                                    sorting, chunk overlap, vault linter     overlap support, docs |
|                                    for archive/log, atomic compiler writes. updates, active specs |
+------------------------------------+----------------------------------------+---------------------+
```

---

## 4. Remediation Plan

### Phase 1: Security Boundaries & Secret Hygiene (B1, B2)
1. **Request-Scoped FastMCP Authentication**:
   - Implement `PyJWT` verification with algorithm enforcement (`HS256`, `RS256`), issuer/audience validation, and expiration/not-before verification.
   - Secure static token comparisons with `hmac.compare_digest`.
   - Wire FastMCP HTTP request header extraction via ASGI middleware/FastAPI dependencies, returning HTTP 401/403 on authentication failure.
   - Remove magic `all` role from caller `Scope` (caller clearance is strictly canonical departments).
2. **Zero-Exemption Secret Scanner**:
   - Update regex to match 20+ character token prefixes (`sk-[A-Za-z0-9_-]{20,}`).
   - Include untracked/staged files (`git ls-files --others --exclude-standard`) and full git commit blob history.
   - Narrow placeholder allowlists in `.gitleaks.toml` and fix trailing whitespace.
   - Add `.gitea/workflows/security.yaml`.

### Phase 2: Host-Sync Isolation & Database Ingest Roles (B3, B4, M1, M9)
1. **Isolated Host-Sync Read Replica**:
   - Update `docker-compose.yml` to mount dedicated named volume `vault-replica` (`/vault-replica`) instead of developer root `./:/repo:rw`.
   - Mount `/vault-replica/wiki` read-only (`:ro`) into `basic-memory`.
   - Update `scripts/host_sync.py` to reject developer repo roots, enforce exact branch refs (`refs/heads/<target>`), maintain atomic snapshots, and start readiness as false until initial sync.
   - Fix `safe.directory` in `scripts/Dockerfile.sync`.
2. **PostgreSQL Ingestion Role RLS & Superuser Removal**:
   - Add explicit `FOR ALL TO rag_ingest_role` RLS policies to `002_rls_and_roles.sql` and `init.sql`.
   - Configure runtime ingestion/sync services to use `rag_ingest_role`, eliminating superuser runtime usage.
   - Add migration integration tests verifying upgrade idempotency and ingest-role DML.

### Phase 3: Closed-Loop CI Address Gate & Department Scope Propagation (B5, M6, M9)
1. **Deterministic CI Gate State Machine**:
   - Implement strict 3-state exit handling:
     - `0`: All addresses PASS & index PASS.
     - `2`: System/network error $\rightarrow$ Fail immediately without mutating files.
     - `1`: Semantic drift on non-main branch $\rightarrow$ Run `scout/healer.py --ci` $\rightarrow$ Post-heal verification gate $\rightarrow$ Exit 0 (healed) or 1 (unhealed).
   - Wire `ci_address_gate.py` into `.gitea/workflows/auto-healer.yaml`.
2. **End-to-End Department Scope Propagation**:
   - Propagate the page's declared department into `Scope(roles=frozenset([page.department]))` across address verification, minting, and healing.
3. **Atomic Page Compiler Contract**:
   - Use `scout.parsers.parse_file` for multi-modal source reading (PDF, CSV, MD).
   - Carry department scope into `mint_address` with explicit `--loc`.
   - Validate candidate page with `scout.vault.lint_page` and implement atomic rollback on failure.

### Phase 4: Deterministic Offline Suite, Mypy & Quality Gates (B6, M2, M3, M5, M8, M10)
1. **Teardown Hang Elimination & Socket Lockdown**:
   - Fix unclosed SQLite `_fts_conn` and background HTTP client teardowns in tests.
   - Add `pytest-socket` configuration in `pyproject.toml` (`--disable-socket --allow-unix-socket`).
   - Fix 3 Mypy type errors in `scout/chunker.py`, `scripts/migrate_postgres.py`, and `scripts/verify_addresses.py`.
2. **Embedding API Ordering & Contextual Overlap**:
   - Sort API embeddings by response `index`.
   - Implement sliding-window character overlap using `overlap_chars`.
   - Unify all embedding errors under `EmbeddingError`.
3. **Canonical Vault Linter & Active Documentation**:
   - Include and validate `archive.md` and `log.md` under vault linting.
   - Add regression tests for FTS title updates and page deletions.
   - Update documentation (`README.md`, `AGENTS.md`, `docs/CONNECT_AGENTS.md`) to reflect the Cloud API + pgvector architecture.

---

## 5. Directives Compliance & Verification Summary

- **Git Directives**: In accordance with user directives, no Git operations (`git commit`, `git push`, `git checkout`) were executed.
- **Diagram Standards**: All architecture diagrams provided in chat responses strictly adhere to ASCII format.
- **Confirmation Gate**: Execution of subsequent code modifications is gated on explicit user authorization.
