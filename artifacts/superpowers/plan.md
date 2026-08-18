# Implementation Plan: Architecture, Security, and Contract Hardening (Codex Rewrite)

### Goal

Remediate the audited credential, authorization, PostgreSQL, host-sync, embedding, cold-start, Scout-DIY, authoring, CI, test-topology, CLI, and documentation defects through incremental, test-first changes on a dedicated feature branch.

Completion means:

- the exposed credential is revoked and redacted without placing replacement secrets in Git or logs;
- Scout derives access from verified server-side identity and a caller can only narrow that access;
- both fresh and existing PostgreSQL volumes use forced RLS plus separate least-privilege query and ingestion roles;
- host-sync publishes an isolated replica and cannot alter the developer checkout;
- embeddings and startup synchronization fail visibly and atomically, never with pseudo-data or false readiness;
- wiki compilation reuses the canonical mechanical contract, minted addresses, safe paths, atomic writes, and PR-first workflow;
- healer CI preserves verifier exit semantics and proves the healed vault passes a second verification;
- offline and service-backed tests are deterministic and separately enforceable;
- operational documentation consistently describes the current Cloud API, three-layer Wiki → Scout → PostgreSQL architecture while preserving historical/raw evidence as history.

### Assumptions

1. Implementation starts on branch `fix/architecture-security-hardening`; nothing is committed or merged directly to `main`.
2. The credential owner will revoke/rotate the exposed LiteLLM credential before repository remediation is considered complete. The replacement is stored only in the approved secret store or untracked `.env`.
3. A Git history rewrite is destructive and is **not** authorized merely by approving this plan. If the all-history scan finds the credential, pause for separate explicit approval and collaborator coordination.
4. Scout authentication modes are `jwt`, `static`, and `development`. Code defaults to fail-closed `jwt`; `development` is explicit and the Compose-published port remains loopback-only.
5. JWT mode uses FastMCP 3.3.1's `JWTVerifier` and request-scoped access token with a fixed asymmetric algorithm, configured issuer, audience, JWKS/public key, expiry, and department claim validation. Tokens are never manually decoded and trusted.
6. Valid wiki page departments are exactly `redteam`, `blueteam`, `ai_eng`, and `infra`. Raw-document ACL value `all` means public to any authenticated department; it is not a caller wildcard. Development scope expands to the four real departments.
7. Security hardening may intentionally require client configuration migration; “backward compatibility” does not preserve unauthenticated or over-privileged behavior.
8. `raw/` and historical proposals remain evidence. Active operational docs are updated; historical docs receive a status banner where needed rather than having past claims silently rewritten.
9. Root `.agent/` and `packages/snp-agent/` copies remain synchronized whenever shared instructions, skills, or workflows change.
10. Production database migration and external credential/history operations occur only after the feature PR is reviewed; implementation verification uses a controlled local/test stack.

### Plan

1. Establish the implementation safety checkpoint (2–5 min)
   - Files: None
   - Change:
     - Record the starting commit and working-tree state; preserve unrelated user changes.
     - Create/switch to `fix/architecture-security-hardening` before any implementation edit and confirm the branch is not protected.
   - Verify: `git status --short && git rev-parse HEAD && git branch --show-current` (expected branch: `fix/architecture-security-hardening`).

2. Revoke the exposed credential and redact the tracked artifact (5–10 min plus provider action)
   - Files: `artifacts/multimodal_system_analysis.md`
   - Change:
     - Credential owner revokes/rotates the exposed value first; never print the old or replacement value in terminal output, tests, commits, or PR text.
     - Replace the tracked credential with an explicit `[REDACTED — credential revoked YYYY-MM-DD]` marker; keep the replacement only in the approved secret store/untracked `.env`.
   - Verify: provider console/API confirms the old credential is rejected; `rg -n '\[REDACTED — credential revoked' artifacts/multimodal_system_analysis.md`; `git diff --check`.

3. Add redacting tracked-tree and history secret scans (5–10 min)
   - Files: `scripts/scan_secrets.py`, `tests/test_secrets_hygiene.py`, `.gitleaks.toml`, `.gitea/workflows/security.yaml`
   - Change:
     - Add a scanner that examines tracked file contents and, in `--history` mode, commit blobs—not only commit messages—while redacting matched values from output and narrowly allowing documented placeholders.
     - Gate pull requests on the tracked-tree scan; document the pinned `gitleaks` all-history command as independent corroboration.
   - Verify: `uv run pytest tests/test_secrets_hygiene.py && uv run python scripts/scan_secrets.py --tracked`; `gitleaks git --redact --no-banner .` when `gitleaks` is installed.

4. Resolve historical exposure through a separately approved incident step (2–10 min decision; rewrite duration separate)
   - Files: Git object history and remote refs only if explicitly approved
   - Change:
     - Run `uv run python scripts/scan_secrets.py --history`; if it finds the revoked credential, stop normal execution and present the exact affected commits/files without revealing the value.
     - Only after explicit approval: create a recoverable repository bundle, perform a targeted `git filter-repo` replacement, force-push coordinated refs, and require collaborators to re-clone. If rewrite is declined, record the accepted residual risk because rotation—not deletion—provides containment.
   - Verify: `uv run python scripts/scan_secrets.py --history` and `gitleaks git --redact --no-banner .` both return 0 after any approved rewrite; remote and a fresh clone produce the same result.

5. Make the offline/integration test boundary real (5–10 min)
   - Files: `pyproject.toml`, `tests/conftest.py`, `tests/integration/conftest.py`, `tests/integration/test_pgvector_backend.py`, `tests/integration/test_ingest_postgres.py`, `tests/test_ingest_v2.py`
   - Change:
     - Register `integration`, add `pytest-socket`, move/split live PostgreSQL tests under `tests/integration/`, and keep parser/chunker/unit tests offline.
     - Integration fixtures fail clearly when an explicitly requested service is absent; they do not turn an integration run green with `pytest.skip`.
   - Verify: `uv run pytest --collect-only -m integration`; `uv run pytest -m 'not integration' --disable-socket --allow-unix-socket`.

6. Centralize department, document-ACL, and Scope contracts (5–10 min)
   - Files: `scout/policy.py`, `scout/types.py`, `tests/test_policy.py`, `tests/test_types.py`
   - Change:
     - Define the four page departments, document ACL values, parsers/validators, and the rule that document ACL `all` is public only after authentication.
     - Replace ambiguous role/team/clearance extraction with one validated `Scope.departments` set; no caller or environment may create a magic `all` scope.
   - Verify: `uv run pytest tests/test_policy.py tests/test_types.py`.

7. Propagate explicit department scope through verify, mint, and healer (5–10 min)
   - Files: `scripts/verify_addresses.py`, `scripts/mint.py`, `scout/healer.py`, `tests/test_verify_addresses.py`, `tests/test_mint.py`, `tests/test_healer.py`
   - Change:
     - Collect each address together with its page department and require an explicit validated Scope for `verify_address`, `verify_all`, and `mint_address`.
     - Make healer preserve the page department during verification and re-minting; remove every internal `Scope(..."all"...)` shortcut and add `--dept` to the standalone mint CLI.
   - Verify: `uv run pytest tests/test_verify_addresses.py tests/test_mint.py tests/test_healer.py`.

8. Implement fail-closed Scout auth configuration and pure scope resolution (5–10 min)
   - Files: `scout/auth.py`, `tests/test_auth.py`
   - Change:
     - Add `jwt` (default), `static`, and `development` configuration parsing. Missing JWT issuer/audience/JWKS/claim settings or empty static departments fail startup.
     - Resolve a verified JWT department claim or static scope into `Scope`; development expands to the four real departments. Reject unknown claim shapes, unknown departments, empty identity, `all`, and expired/not-yet-valid identity context.
   - Verify: `uv run pytest tests/test_auth.py`.

9. Wire FastMCP JWT verification and request identity (5–10 min)
   - Files: `scout/mcp_server.py`, `scout/serve.py`, `tests/test_mcp_server.py`
   - Change:
     - Construct FastMCP with `JWTVerifier` in JWT mode using a fixed asymmetric algorithm plus configured issuer, audience, and JWKS/public key; access claims through `CurrentAccessToken` dependency injection.
     - Keep static/development scope server-owned, reject invalid configuration before binding a socket, and ensure authentication failures are protocol/tool errors rather than `no_source` results.
   - Verify: `uv run pytest tests/test_auth.py tests/test_mcp_server.py`.

10. Allow caller department only as a narrowing filter (5–10 min)
    - Files: `scout/mcp_server.py`, `tests/test_mcp_server.py`, `tests/test_mcp_http_auth.py`
    - Change:
      - Add optional `department: str | list[str] | None`; intersect it with the verified base Scope and reject any unknown or unauthorized request before invoking the backend.
      - Test valid narrowing, omitted narrowing, attempted broadening, empty intersection, malformed department input, invalid/missing JWT, and a backend spy proving denial performs no retrieval.
    - Verify: `uv run pytest tests/test_mcp_server.py tests/test_mcp_http_auth.py`.

11. Create an idempotent PostgreSQL migration path (5–10 min)
    - Files: `config/postgres/init.sql`, `config/postgres/migrations/001_schema.sql`, `config/postgres/migrations/002_roles_rls.sql`, `scripts/migrate_postgres.sh`, `tests/integration/test_postgres_migrations.py`
    - Change:
      - Turn `init.sql` into an idempotent migration entrypoint with a migration ledger; use the same files for a new cluster and upgrades of an existing V2 schema.
      - Make the migration command transactional and `ON_ERROR_STOP`; never depend on Docker's first-boot-only behavior for upgrades.
    - Verify: `docker compose config --quiet`; `uv run pytest -m integration tests/integration/test_postgres_migrations.py -k fresh`.

12. Provision least-privilege query and ingestion roles without repository passwords (5–10 min)
    - Files: `config/postgres/migrations/002_roles_rls.sql`, `docker-compose.yml`, `docker-compose.integration.yml`, `.env.example`, `scripts/bootstrap.sh`, `scout/backends/pgvector.py`, `scout/ingest.py`, `tests/test_bootstrap.py`
    - Change:
      - Use fixed role names with passwords obtained inside `psql` via environment/secret input; remove hardcoded/fallback passwords and have bootstrap generate local untracked values.
      - Use `rag_app_role` for SELECT and a separate `rag_ingest_role` for required SELECT/DML/sequence access; sync-job must no longer connect as PostgreSQL superuser. Do not publish PostgreSQL from the production Compose file; add a loopback-only integration override, and bind Scout's host port to loopback by default.
    - Verify: `uv run pytest tests/test_bootstrap.py`; `docker compose config --quiet`; `docker compose -f docker-compose.yml -f docker-compose.integration.yml config --quiet`; `rg -n "rag_app_secret|postgres_master_secret" config scout scripts docker-compose.yml` returns no runtime fallback credential.

13. Enforce document and chunk RLS with defined public-document semantics (5–10 min)
    - Files: `config/postgres/migrations/002_roles_rls.sql`, `scout/backends/pgvector.py`, `tests/integration/test_postgres_rls.py`
    - Change:
      - Enable and force RLS on `rag_documents` and `rag_chunks`; require a nonempty transaction-local department setting before either department overlap or document ACL `all` can match.
      - Add explicit policies for the query role and full-corpus-but-non-superuser ingestion role; keep `set_config(..., true)` transaction-local and validate every Scope before SQL.
    - Verify: `uv run pytest -m integration tests/integration/test_postgres_rls.py` covering positive access, cross-department denial, public documents, missing/invalid scope, direct table enumeration, joins, and pooled connection reuse.

14. Prove both fresh-install and existing-volume upgrades (5–10 min)
    - Files: `tests/integration/test_postgres_migrations.py`, `docs/runbook.md`
    - Change:
      - Test migration from an old schema containing the hardcoded role/missing document RLS and test idempotent reapplication against the upgraded schema.
      - Document the reviewed production upgrade command, pre-migration backup, post-migration role/RLS inspection, and the fact that normal container restart alone is insufficient.
    - Verify: `uv run pytest -m integration tests/integration/test_postgres_migrations.py`; run `scripts/migrate_postgres.sh --check` twice against the integration database with both runs returning 0.

15. Specify host-sync isolation with regression tests first (5–10 min)
    - Files: `tests/test_host_sync.py`
    - Change:
      - Build temporary upstream, dedicated replica, and developer repositories; assert synchronization changes only the replica and leaves developer tracked, modified, deleted, renamed, and untracked files/status untouched inside and outside `wiki/`.
      - Test configured branch filtering, invalid payload behavior, startup sync, failed fetch, serialized webhooks, and readiness remaining false after failure.
    - Verify: `uv run pytest tests/test_host_sync.py` (expected regression failures before implementation, then pass after Steps 16–17).

16. Implement a dedicated replica and atomic published snapshot (5–10 min)
    - Files: `scripts/host_sync.py`, `scripts/Dockerfile.sync`, `.gitignore`, `tests/test_host_sync.py`
    - Change:
      - Sync only a validated dedicated path under `.host-sync/`: update an internal replica, materialize a commit-addressed wiki snapshot, then atomically switch a `current` pointer. Never accept the workspace root, never run Git against the developer checkout, and remove wildcard global `safe.directory` configuration.
      - Use `GIT_BRANCH` consistently for fetch and webhook filtering; validate all cleanup targets as children of the dedicated replica root and expose separate liveness/readiness state.
    - Verify: `uv run pytest tests/test_host_sync.py`; `rg -n "safe.directory.*\*|reset.*--hard.*VAULT_DIR" scripts/host_sync.py scripts/Dockerfile.sync` returns no unsafe match.

17. Rewire Compose and basic-memory to the isolated replica (5–10 min)
    - Files: `docker-compose.yml`, `basic-memory/config.json`, `scripts/bootstrap.sh`, `.env.example`, `tests/test_host_sync.py`
    - Change:
      - Replace the repository-root read-write mount with `./.host-sync:/replica`; mount that parent read-only into basic-memory and point its project at `/replica/current/wiki`.
      - Bootstrap the replica directory, require remote/branch/webhook configuration, perform startup sync before readiness, and make basic-memory wait for a ready replica without exposing Git credentials to it.
    - Verify: `docker compose config --quiet`; `docker compose build host-sync basic-memory`; `uv run pytest tests/test_host_sync.py`.

18. Remove pseudo-vector fallback and validate embedding responses (5–10 min)
    - Files: `scout/chunker.py`, `scout/backends/pgvector.py`, `tests/test_chunker.py`, `tests/test_pgvector_backend_unit.py`
    - Change:
      - Add `EmbeddingError`; production embedder always raises on timeout, HTTP/API/schema error, wrong batch size, wrong dimension, or non-finite values. Remove runtime mock/environment fallback completely.
      - Unit tests inject a fake embedder. Query-time embedding failure propagates as an error and is never translated to `no_source` or an empty pseudo-success.
    - Verify: `uv run pytest tests/test_chunker.py tests/test_pgvector_backend_unit.py --disable-socket --allow-unix-socket`.

19. Make ingestion atomic and retry failures without fake data (5–10 min)
    - Files: `scout/ingest.py`, `scout/sync_job.py`, `tests/test_ingest_v2.py`, `tests/test_sync_job.py`
    - Change:
      - Compute/validate embeddings and replace each document inside a transaction so failure preserves the last good document/chunks; log only paths/error classes, never content or credentials.
      - Add dependency-injected bounded exponential retry for transient embedding/database failures; after exhaustion return/raise a failed outcome so the service becomes unhealthy/restarts instead of silently continuing.
    - Verify: `uv run pytest tests/test_ingest_v2.py tests/test_sync_job.py --disable-socket --allow-unix-socket` including partial-batch rollback and retry-exhaustion tests.

20. Add fail-fast cold-start synchronization and readiness (5–10 min)
    - Files: `scout/sync_job.py`, `docker-compose.yml`, `tests/test_sync_job.py`
    - Change:
      - Refactor to testable `async_main()`: run one initial sync before the watcher, exit nonzero on `IndexOutcome(ok=False)`, and create readiness state only after success. Do not add an unused `initial_sync` parameter to `watch()`.
      - Keep finite watcher tests unchanged and ensure a watched-batch failure follows the same retry/fail-visible policy.
    - Verify: `uv run pytest tests/test_sync_job.py`; container healthcheck remains unhealthy when the initial fake indexer fails and healthy after a successful cold start.

21. Repair Scout-DIY vault loading and FTS recovery (5–10 min)
    - Files: `scout/diy_engine.py`, `tests/test_diy_engine.py`
    - Change:
      - Replace the dead `spikes._lib.vault` import with `scout.vault` and keep the test vault isolated under `tmp_path`.
      - Rebuild SQLite FTS from all pages when `search.db` is missing/empty/stale even if vector embeddings are already cached; test changed and deleted pages as well as the missing-database case.
    - Verify: `uv run pytest tests/test_diy_engine.py --disable-socket --allow-unix-socket`.

22. Make `scout.vault` the complete mechanical contract (5–10 min)
    - Files: `scout/vault.py`, `scripts/gen_index.py`, `tests/test_vault.py`, `tests/test_gen_index.py`, `wiki/archive.md`, `wiki/log.md`, `wiki/playbooks/llm-outage-failover.md`, `wiki/playbooks/prompt-injection-incident-response.md`, `wiki/techniques/indirect-injection-defense.md`, `wiki/index.md`
    - Change:
      - Validate allowed type/department, all seven fields, one-line one-sentence summary, entities list, ISO date, source shape and normalized `raw/` containment, forbidden `related`, and exact ordered body headings. Preserve broken-link/orphan behavior as warnings per `AGENTS.md`.
      - After the user approves the explicit semantic mapping, migrate `sre → infra`, `security → blueteam`, and navigational page headings to the contract; regenerate—not hand-edit—`wiki/index.md` and retain existing source addresses unless separately re-minted.
    - Verify: `uv run pytest tests/test_vault.py tests/test_gen_index.py && python scripts/gen_index.py --check`.

23. Harden compiler input extraction and path containment (5–10 min)
    - Files: `scripts/compile_note.py`, `scout/parsers.py`, `tests/test_compile_note.py`
    - Change:
      - Resolve source paths strictly beneath `raw/`, accept only parser-supported formats through `scout.parsers.parse_file`, bound prompt size, and reject symlink/absolute/`..` escapes before reading or calling LiteLLM.
      - Use canonical type-to-directory mapping and a safe slug whose final resolved output remains beneath the expected `wiki/<category>/`; reject overwrite unless an explicit reviewed option is supplied.
    - Verify: `uv run pytest tests/test_compile_note.py -k 'path or parser or slug or overwrite' --disable-socket --allow-unix-socket`.

24. Make compilation fail-closed, minted, and atomic (5–10 min)
    - Files: `scripts/compile_note.py`, `scout/vault.py`, `tests/test_compile_note.py`
    - Change:
      - Require valid `--dept` and `--loc`; validate LiteLLM JSON without fallback summaries/entities/hints; pass department Scope to mint and abort on FAIL/DRIFT before writing.
      - Render a candidate Page and validate it with `scout.vault`; atomically write the page and regenerate the index, restoring previous page/index bytes on any failure. Cross-references come only from validated optional wikilinks, not an automatic `[[index]]` placeholder.
    - Verify: `uv run pytest tests/test_compile_note.py --disable-socket --allow-unix-socket`; failure tests assert neither page nor index changes.

25. Enforce branch-before-write and exact-page PR proposal (5–10 min)
    - Files: `scripts/compile_note.py`, `scripts/propose_page.py`, `tests/test_compile_note.py`, `tests/test_propose_page.py`, `.agent/skills/snp-compile-wiki/SKILL.md`, `packages/snp-agent/skills/snp-compile-wiki/SKILL.md`
    - Change:
      - Refuse compiler writes on `main`/`master`; require the user/agent to create or select a wiki feature branch before generation. Keep Git/network side effects outside content generation.
      - Make `propose_page.py` operate on the current non-protected branch, stage only the named page plus generated index/log when applicable, reject unrelated wiki changes, lint/verify before commit, push only with explicit `--push`, and never merge.
    - Verify: `uv run pytest tests/test_compile_note.py tests/test_propose_page.py`; compare mirrored skill files with `diff -u .agent/skills/snp-compile-wiki/SKILL.md packages/snp-agent/skills/snp-compile-wiki/SKILL.md`.

26. Encode the address-heal exit-state machine in tested code (5–10 min)
    - Files: `scripts/ci_address_gate.py`, `scripts/verify_addresses.py`, `scout/healer.py`, `tests/test_ci_address_gate.py`, `tests/test_healer.py`
    - Change:
      - Implement the exact gate: verifier `0` passes without heal; `1` permits one heal then mandatory full re-verification; `2` or any unexpected code fails immediately without heal; failed re-verification fails and leaves no commit.
      - Make both CI in-place and scheduled proposal modes verify the final working tree before commit/push and preserve PR-first cleanup without discarding pre-existing user changes.
    - Verify: `uv run pytest tests/test_ci_address_gate.py tests/test_healer.py` with table-driven 0/1/2/heal-failure/reverify-failure cases.

27. Replace YAML error swallowing with the tested gate (5–10 min)
    - Files: `.gitea/workflows/auto-healer.yaml`, `tests/test_workflow_config.py`
    - Change:
      - Invoke `ci_address_gate.py` in PR and scheduled paths, run vault lint after mutation, and commit/push only after final verification succeeds. Infrastructure failure never sets a drift flag.
      - Pin workflow actions, use the non-superuser query role, avoid default credentials, fetch full history where secret scanning requires it, and assert step ordering structurally.
    - Verify: `uv run pytest tests/test_workflow_config.py`; `actionlint .gitea/workflows/auto-healer.yaml .gitea/workflows/security.yaml` when `actionlint` is installed.

28. Complete CLI behavior and tests (5–10 min)
    - Files: `scripts/ingest_v2.py`, `scripts/export_mcp_config.py`, `tests/test_ingest_v2.py`, `tests/test_export_mcp_config.py`, `docs/CONNECT_AGENTS.md`
    - Change:
      - Make `--dept` an alias for the same validated comma-separated document ACL destination as `--allowed-depts`; accept only four departments plus document-public `all`.
      - Make `--client` and `--all` mutually exclusive, prompt only on a TTY when neither is supplied, and return argparse exit 2 in noninteractive mode. Test every supported client, `--all`, TTY selection, cancellation, and non-TTY failure.
    - Verify: `uv run pytest tests/test_ingest_v2.py tests/test_export_mcp_config.py`; `python scripts/ingest_v2.py --help`; `python scripts/export_mcp_config.py --print --all`.

29. Inventory documentation by authority before editing claims (5–10 min)
    - Files: `docs/ARCHITECTURE_STATUS.md`, `tests/test_docs_contract.py`
    - Change:
      - Classify root/active docs, `.agent`/package instructions, code/config comments, wiki compiled knowledge, generated files, historical proposals/sprints, artifacts, and immutable raw evidence.
      - Define search scopes and permitted historical exceptions for Ollama, RAG-Anything, DOCX, 3072 dimensions, “bi-temporal,” service counts, and architecture terminology; tests must not demand rewriting `raw/` or historical evidence.
    - Verify: `uv run pytest tests/test_docs_contract.py -k inventory`; `rg -n -i 'ollama|rag-anything|docx|3072|bi-temporal' README.md AGENTS.md docs .agent packages scout config scripts` is fully classified in `docs/ARCHITECTURE_STATUS.md`.

30. Align active docs, mirrored agent guidance, code comments, and wiki knowledge (5–10 min per coherent document group)
    - Files: `README.md`, `AGENTS.md`, `pyproject.toml`, `docs/runbook.md`, `docs/basic-memory-setup.md`, `docs/CONNECT_AGENTS.md`, `docs/DEMO.md`, `docs/SESSION_HANDOVER_AND_V2_ROADMAP.md`, `docs/sprint/IMPLEMENTATION_PLAN.md`, `config/litellm/config.yaml`, `scout/types.py`, `scout/core.py`, `scout/workflow.py`, `scout/serve.py`, `scout/sync_job.py`, `scout/diy_engine.py`, `scout/mcp_server.py`, `scout/backends/__init__.py`, `scout/backends/fake.py`, `scripts/mint.py`, `scripts/verify_addresses.py`, `.agent/instructions/agent_guide.instructions.md`, `.agent/instructions/coding_standards.instructions.md`, `.agent/skills/snp-bootstrap-system/SKILL.md`, `.agent/skills/snp-ingest-raw-data/SKILL.md`, `.agent/skills/snp-rag-fetch/SKILL.md`, `.agent/skills/snp-verify-vault/SKILL.md`, `packages/snp-agent/instructions/agent_guide.instructions.md`, `packages/snp-agent/workflows/snp-ingest.md`, `packages/snp-agent/workflows/snp-query.md`, `packages/snp-agent/workflows/snp-verify.md`, `packages/snp-agent/skills/snp-bootstrap-system/SKILL.md`, `packages/snp-agent/skills/snp-ingest-raw-data/SKILL.md`, `packages/snp-agent/skills/snp-rag-fetch/SKILL.md`, `packages/snp-agent/skills/snp-verify-vault/SKILL.md`, `wiki/concepts/model-routing-gateway.md`, `wiki/entities/gemini-embedding-pipeline.md`, `wiki/index.md`
    - Change:
      - Describe two knowledge stores but the operational three-layer path (Wiki → Scout → PostgreSQL), Cloud API routing, actual parser formats, actual vector dimension/configuration, auth modes, migrations, isolated host-sync, and current service responsibilities.
      - Add archival banners to superseded handover/sprint material instead of rewriting its historical narrative; update wiki pages through the normal provenance/last-compiled/index flow and explicitly record source-vs-current-implementation conflicts.
    - Verify: `uv run pytest tests/test_docs_contract.py tests/test_agent_package.py`; `python scripts/gen_index.py --check`; `diff -u` for each mirrored root/package skill or instruction pair changed in this step.

31. Run the complete offline quality gate (5–10 min)
    - Files: None (verification only)
    - Change:
      - Run all non-integration tests with sockets disabled, then formatting/lint/type/vault/doc/secret checks. Regenerate `wiki/index.md` before the final `--check` only if approved wiki files changed.
      - Fix failures in the owning earlier step; do not weaken assertions, broad-allow secrets, or unmark live tests to make the gate green.
    - Verify: `uv run pytest -m 'not integration' --disable-socket --allow-unix-socket && uv run ruff check . && uv run mypy scout scripts && python scripts/gen_index.py --check && uv run python scripts/scan_secrets.py --tracked && git diff --check`.

32. Run fresh-install, upgrade, and service-backed integration gates (5–10 min per gate)
    - Files: None (verification only)
    - Change:
      - Validate Compose, build changed images, start PostgreSQL/LiteLLM/Scout/sync-job/host-sync/basic-memory with bounded health waits, apply migrations twice, and run integration tests against non-superuser roles.
      - Exercise valid/invalid JWT MCP calls, cross-department RLS, public documents, pool reuse, cold-start indexing, host-sync readiness, and full address verification. Do not remove or reset any non-test volume.
    - Verify: `docker compose config --quiet && docker compose build scout host-sync basic-memory`; `docker compose -f docker-compose.yml -f docker-compose.integration.yml up -d --wait postgres litellm scout sync-job host-sync basic-memory`; `scripts/migrate_postgres.sh --check && scripts/migrate_postgres.sh --check`; run `uv run pytest -m integration` with the override's loopback database endpoint; `uv run python scripts/verify_addresses.py`; `docker compose -f docker-compose.yml -f docker-compose.integration.yml ps` shows every required service healthy.

33. Perform final security review and propose the PR (5–10 min)
    - Files: `artifacts/superpowers/review.md`, `artifacts/superpowers/finish.md`
    - Change:
      - Review the resulting diff by Blocker/Major/Minor/Nit, confirm no credential value appears, and map every acceptance criterion to passing evidence. Resolve all blockers/majors before proposal.
      - Commit atomic step groups on the feature branch, push only with user-approved credentials, open a PR to `main`, and stop for human review—never auto-merge.
    - Verify: `git status --short`; `git log --oneline main..HEAD`; repeat Step 31 plus the required Step 32 gates; confirm the PR base/head are `main <- fix/architecture-security-hardening` and merge status remains pending.

### Risks & mitigations

- **Credential/history operations:** Revocation is irreversible but necessary; never restore the old key. History rewriting is isolated behind separate approval, a repository bundle, a communicated maintenance window, and fresh-clone verification.
- **Authentication migration:** Existing unauthenticated clients will fail by design. Provide explicit local `development` configuration bound to loopback, static single-tenant configuration, JWT examples, and MCP exporter updates before changing shared deployments.
- **JWT configuration mistakes:** Fail startup unless algorithm, issuer, audience, verifier key/JWKS, and department claim are present. Use FastMCP verification, not custom token decoding, and test invalid signature/issuer/audience/time/claim cases.
- **RLS lockout or leakage:** Keep admin migration credentials separate, add a least-privilege ingestion role, use transaction-local scope, test reused pooled connections, and verify fresh plus upgraded schemas before production migration.
- **Persistent-volume upgrades:** Never assume `init.sql` reruns. Take a database backup, apply the idempotent migration explicitly, inspect policies/roles, and avoid `docker compose down -v` on any non-test project.
- **Host-sync data loss:** Remove the workspace-root mount entirely. Validate replica roots, publish commit-addressed snapshots atomically, retain the previous snapshot for quick fallback, and test that developer status/content never changes.
- **Transient model/database failures:** Use bounded retries with injected clocks in tests, transactional document replacement, observable unhealthy/failed state, and container restart policy; never substitute pseudo-vectors or fabricated wiki data.
- **Stricter wiki validation:** Mechanically migrate existing invalid departments/headings on the feature branch, preserve/mint addresses as required, regenerate the index, and keep human review for semantic/provenance changes.
- **Documentation history:** Update active guidance and code comments while banner-marking superseded documents. Do not rewrite raw evidence or prior proposals simply to satisfy a global text search.
- **Integration flakiness:** Use explicit markers, bounded timeouts/health waits, deterministic fixtures, and failure—not skip—when an integration gate is intentionally selected.

### Rollback plan

- Keep implementation in atomic commits on `fix/architecture-security-hardening`; revert the smallest failed commit on that branch or discard the unmerged branch. Never use `git reset --hard` or checkout-based cleanup against the developer workspace.
- Credential revocation is not rolled back. If the replacement configuration fails, issue another replacement; never re-enable or recommit the exposed value.
- A history rewrite, if separately approved, requires a pre-rewrite `git bundle`, recorded old/new ref mapping, coordinated remote maintenance, and re-clones. Restoring refs is a deliberate incident decision, not an automatic script action.
- Database migrations are forward-applied only after backup. Test rollback against a disposable database; in production, restore from the verified backup or apply a reviewed compensating migration rather than deleting `pgdata`.
- Host-sync retains the last known-good published snapshot. On failure, repoint `current` to that validated snapshot and stop the webhook service; the developer checkout remains untouched.
- Auth rollout can temporarily select a previously tested **static** single-tenant scope while JWT provider configuration is repaired. Do not fall back to development mode on a shared/non-loopback deployment.
- No workflow pushes or PR merges until all offline and required integration gates are green. A failed plan execution leaves only an unmerged feature branch and explicitly reported external-state changes.
