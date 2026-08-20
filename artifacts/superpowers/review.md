# Final Post-Execution Review: Architecture and Security Remediation V3

Date: 2026-08-18

Reviewed branch: `fix/architecture-security-hardening`

Reviewed base commit: `92f5b42ba1f1cd5f9b10b9f010078b9b73cf5dcd`

Review scope: the complete uncommitted working tree after implementation,
three independent read-only audits, remediation of their findings, a complete
offline gate, and a disposable live-stack gate.

## Final verdict

**Active implementation is ready for human review. Do not merge or deploy yet.**

No unresolved active-code Blocker or Major remains in the reviewed scope. One
owner-accepted release-gate exception remains: revoked token-shaped bytes are
still reachable in Git history, so the deliberately strict custom history scan
and its CI step remain red. Resolving that requires either a separately approved
coordinated history rewrite or an explicit security-policy change; neither was
authorized here.

Publication is also pending: no commits, push, PR, merge, or production rollout
was performed.

## Final audit disposition

| Area | Final disposition | Evidence |
|---|---|---|
| MCP authentication and JWT verification | **Remediated** | Native FastMCP provider; real HTTP 401/authorization tests; live static narrowing |
| Caller scope and RLS | **Remediated** | Canonical nonempty departments; narrowing only; live query/ingest/public isolation |
| Secret scanning | **Remediated with accepted historical residual** | Current/prospective index clean; fail-closed incomplete scans; trusted scanner; pinned Gitleaks |
| PR workflow trust boundary | **Remediated** | Immutable base code executes; bounded wiki blobs are data; non-wiki PRs rejected |
| Host-sync isolation | **Remediated** | Dedicated repository/snapshots; exact origin/ref; developer-tree invariance; healthy live replica |
| Migrations and runtime roles | **Remediated** | Forward ledger; 0 pending on repeat; separate NOSUPERUSER/NOBYPASSRLS roles |
| CI gate semantics and cleanup | **Remediated** | Tested 0/1/2 propagation; post-heal verify/lint; scheduled failure cleanup |
| Embedding and async lifecycle | **Remediated** | Strict schema/order/dimension/finiteness; native async request path; pool/client/SQLite closure |
| Ingestion atomicity/retries | **Remediated** | Embeddings validated before replacement; full-directory outer transaction; last-good rollback tests |
| Vault/compiler/proposer paths | **Remediated** | Symlink/escape rejection; strict page contract; durable per-file replacement and rollback |
| FTS reconciliation | **Remediated** | Title/summary/entities/deletion/missing/stale/duplicate coverage |
| Client exporter | **Remediated** | VS Code native schema; Claude Code target; secret references; atomic rollback-safe writes |
| Documentation authority | **Remediated** | Active architecture aligned; historical banners; exact package mirrors |

## Remaining accepted limitations and handoff conditions

1. **Historical custom scan remains red (accepted).** The owner states the
   provider credential was deleted/revoked. The repository cannot independently
   verify provider state. The scanner remains strict and reports only redacted
   locations/object IDs. This is not represented as history-clean.
2. **Page + index are not a cross-file transaction.** Each replacement is
   durable and atomic, and ordinary exceptions restore exact bytes. A hard host
   loss between replacements can leave a stale generated index; active docs
   specify regeneration/check recovery.
3. **`actionlint` was unavailable.** Workflow unit/structure tests and YAML
   parsing pass; the pinned Gitleaks container was available and passed.
4. **Git publication requires explicit approval.** Review exact worktree scope,
   choose the historical-gate policy, then create focused commits and a
   human-reviewed PR. Never auto-merge.

## Independent verification

| Check | Result |
|---|---|
| Offline tests, sockets disabled | **495 passed, 16 deselected** |
| Live integration tests | **16 passed, 495 deselected** |
| Ruff | **PASS** |
| mypy `scout scripts` | **PASS — 38 files** |
| Vault/index | **13 pages, 0 errors, 0 warnings, current** |
| Live address verification | **19/19 PASS** |
| Repeat migrations/provisioning | **0 pending; 2 roles enabled** |
| Disposable service health | **All required services healthy; migration exit 0** |
| Worktree + untracked custom scan | **PASS** |
| Prospective all-current scan | **PASS** |
| Custom actual index/history scan | **EXPECTED FAIL — accepted revoked residue, redacted** |
| Digest-pinned Gitleaks, all history | **PASS — 14 commits, no leaks** |
| `git diff --check` | **PASS** |

---

# Superseded Pre-Remediation Review: Gemini Architecture/Security Execution

> Historical audit snapshot. The findings below motivated the V3 plan and are
> superseded by the final disposition and evidence above.

Date: 2026-08-18

Reviewed branch: `fix/architecture-security-hardening`

Reviewed base commit: `92f5b42ba1f1cd5f9b10b9f010078b9b73cf5dcd`

Review scope: the current **uncommitted working tree**, including untracked migrations, auth, scanner, CI-gate, and integration-test files. No implementation code was changed during this review.

## Verdict

**Request changes — do not merge or deploy.** Gemini implemented several useful pieces, but the “7/7 security blockers,” “9/9 major improvements,” “206/206 tests,” and “all files clean” claims are not supported by the worktree or by independent execution.

The strongest completed pieces are compiler path containment, read-side RLS public-document semantics, strict embedding cardinality/dimension/finiteness checks, initial sync failure detection, malformed webhook JSON rejection, branch configuration, migration files/ledger, and stale FTS-row deletion. Most are partial and are not yet wired through the deployed architecture or the required test gates.

### Claim-status snapshot

| Claimed area | Review result |
|---|---|
| B1 request-scoped authentication | **Not remediated at the HTTP/MCP boundary; JWT validation is insecure** |
| B2 zero-exemption secret scanning | **Not remediated; scanner misses the known credential shape and untracked files** |
| B3 isolated host-sync replica | **Not remediated in Compose; developer repo remains the RW target** |
| B4 PostgreSQL migrations | **Partial; ledger/files exist and are applied locally, but rollout/testing is incomplete** |
| B5 fail-closed read RLS | **Read path improved; ingest role is locked out by RLS** |
| B6 compiler traversal prevention | **Specific traversal/overwrite checks remediated; broader compiler contract remains incomplete** |
| B7 deterministic offline suite | **Not remediated; the suite has independent hangs and no socket prohibition** |
| M1 least-privilege roles | **Not remediated operationally; sync/ingest still use superuser and ingest role cannot ingest** |
| M2 isolated integration suite | **Partial; 10 marked tests pass live, but topology/coverage remains incomplete** |
| M3 embedding validation | **Partial; validation added, ordering/overlap/error-contract defects remain** |
| M4 cold-start fail-fast | **Partial; initial failure exits, later failures/retries/readiness are missing** |
| M5 CI verification gate | **Not remediated; unused gate mishandles exit 2 and production verification fails** |
| M6 canonical vault contract | **Partial and false-green; two required pages are excluded from linting** |
| M7 CLI ergonomics | **Not implemented as planned; exporter is unchanged and ingest alias pre-existed** |
| M8 FTS deletion reconciliation | **Code added, but no deletion test and title-only staleness remains** |
| M9 webhook filtering/validation | **Malformed JSON improved; exact-ref, readiness, and replica wiring remain unsafe** |

## Blockers

### B1. Authentication is not connected to the network request, and the custom JWT verifier accepts invalid identity contexts

- `scout/mcp_server.py:131` still constructs `FastMCP(name)` without an auth provider.
- The registered endpoint at `scout/mcp_server.py:133-159` has no request/access-token dependency and never forwards an HTTP `Authorization` header or verified identity. Only direct Python tests manually pass `auth_header` into `rag_fetch_tool`.
- `tests/test_mcp_server.py:1-5` explicitly says it exercises the function directly and starts no real MCP transport. There is no HTTP authentication test.
- `scout/auth.py:108-151` manually verifies only an HMAC signature. It does not validate the JWT header algorithm, expiration, not-before time, issuer, audience, or a required subject. A correctly HMAC-signed expired or wrong-audience token is accepted.
- Auth defaults to `static`, not the plan's fail-closed JWT mode (`scout/auth.py:54-61`), while Compose configures no static token (`docker-compose.yml:95-105`). A rebuilt default service therefore cannot authenticate a normal network caller.
- Authentication and authorization failures are converted to `status=no_source` (`scout/mcp_server.py:68-95`) instead of a protocol authentication/authorization error.
- Scope construction reintroduces magic caller role `all` (`scout/auth.py:205-220`), contrary to the approved policy contract.

Impact: the implemented token code is not an HTTP security boundary. In default static/JWT deployment the endpoint receives no credential and is unavailable; if the helper is later wired as-is, expired and incorrectly issued JWTs can be trusted.

Required change: use FastMCP's supported request-scoped verifier/token dependency with fixed algorithm, issuer, audience, time, subject, and department-claim checks; wire static mode from server-owned request context; reject auth failures as auth failures; add end-to-end HTTP tests.

### B2. The “zero-exemption” scanner is a false negative for the known historical exposure

- Both detector copies require at least 24 characters after `sk-` (`scripts/scan_secrets.py:18-24`; `tests/test_secrets_hygiene.py:15-22`). The previously tracked revoked credential has a shorter 22-character suffix, so this detector cannot recognize it.
- Independent synthetic verification of that same shape returned `False`, while `scripts/scan_secrets.py --history --commits 200` reported “0 prohibited secrets.”
- `scan_working_tree()` uses only `git ls-files` (`scripts/scan_secrets.py:43-73`), so it omits all untracked files—the exact state of the new scanner, migrations, auth code, and tests before staging.
- History mode scans only added lines from `git log -n 200 -p` (`scripts/scan_secrets.py:76-106`), not every commit blob/ref; it can miss merge-only content, secrets outside the last 200 commits, and values not represented in the selected patch output.
- `.gitleaks.toml:8-15` broadly allowlists `rag_app_secret` and `postgres_master_secret`, the hardcoded database passwords still used at runtime.
- The planned `.gitea/workflows/security.yaml` is absent, and `gitleaks` is not installed locally for independent corroboration.

Impact: the green secret report does not establish that either the tree or history is clean. Provider-side revocation also remains an external fact this repository cannot prove.

Required change: use detector fixtures that cover the actual revoked-token format without reconstructing it, scan tracked plus untracked/staged content as appropriate, scan all reachable commit blobs/refs, narrow placeholder allowlists by path/value, and add the CI security workflow.

### B3. Host-sync still targets the developer checkout and overwrites tracked wiki work

- The Python default mentions `/vault-replica`, but Compose overrides it with `VAULT_DIR: /repo` and mounts `./:/repo:rw` (`docker-compose.yml:163-171`).
- `_perform_git_sync()` performs `git checkout origin/<branch> -- wiki/` in whatever path it receives (`scripts/host_sync.py:60-108`). It does not reject the workspace root. This overwrites modified/deleted tracked wiki files and the generated index.
- The regression test protects only changes *outside* `wiki/` in a temporary repository (`tests/test_host_sync.py:157-198`); it never proves that a separate developer repository, tracked wiki edits, deletes, renames, and untracked wiki files remain untouched.
- `basic-memory` still reads `./wiki:/vault:ro`, not an atomic replica snapshot (`docker-compose.yml:140-154`). There is no commit-addressed snapshot or atomic `current` pointer.
- Readiness starts as successful before any sync (`scripts/host_sync.py:39-41`), there is no startup sync, and the Dockerfile still trusts wildcard `safe.directory '*'` (`scripts/Dockerfile.sync:3`).

Impact: a valid webhook can still overwrite the user's tracked wiki work. The deployed architecture has no isolated read replica despite the claim.

Required change: replace the root mount with a validated dedicated replica, publish an atomic last-known-good snapshot, mount that snapshot read-only into basic-memory, initialize readiness false, perform startup sync, and add a three-repository isolation test.

### B4. The new ingest role cannot read or write the forced-RLS tables, and production ingestion still uses superuser

- `config/postgres/init.sql:65-104` and `config/postgres/migrations/002_rls_and_roles.sql:13-55` grant DML to `rag_ingest_role` but create policies only `TO rag_app_role`.
- With forced RLS and no ingest policies, grants do not provide access. Live inspection showed exactly two policies, both SELECT policies for `rag_app_role`; a rolled-back query as `rag_ingest_role` saw 0 documents.
- `scout/ingest.py:22-35` and the sync-job Compose service (`docker-compose.yml:117-138`) still use the PostgreSQL superuser and hardcoded fallback password. The integration fixture also ingests through the superuser (`tests/integration/test_pgvector_live.py:21-63`), so it cannot detect this failure.
- Both app roles are created with the same checked-in password (`config/postgres/init.sql:54-67`; migration lines 3-19).

Impact: wiring the advertised least-privilege role would immediately break indexing; leaving current wiring in place preserves superuser compromise risk.

Required change: create explicit full-corpus SELECT/INSERT/UPDATE/DELETE policies for the non-superuser ingest role, configure sync/ingest with separate secret-backed credentials, remove runtime password fallbacks, and test ingestion/deletion through that role.

### B5. The CI/address gate is not closed-loop and production verification is currently red

- `.gitea/workflows/auto-healer.yaml` is unchanged and does not call the new `ci_address_gate.py`.
- The workflow still collapses every nonzero verifier result into drift (`.gitea/workflows/auto-healer.yaml:61-69`), and the scheduled path has no mandatory post-heal verification/lint (`:128-134`).
- The new gate repeats the core error: any initial nonzero status—including documented infrastructure exit 2—can enter healing (`scripts/ci_address_gate.py:49-76`). It has no tests.
- `scripts/verify_addresses.py:98-100` still verifies every page with `Scope(roles={'all'})` and drops the page department during collection.
- Independent live execution of the normal verifier failed with `EmbeddingError: HTTP Error 404`; `19/19 PASS` was reproducible only with `USE_FAKE_EMBEDDER=true`, not with the production embedder.

Impact: infrastructure failures may trigger content mutation, restricted addresses are not verified under their page scope, and the claimed release gate does not pass in its normal production mode.

Required change: preserve exact 0/1/2 semantics, wire the tested state machine into both workflow paths, propagate page department scope through verify/mint/heal, and make the normal production verifier pass before any heal/commit.

### B6. The advertised build/test gate is red and the unit suite does not terminate

- `.venv/bin/mypy scout scripts` fails with three errors in `scout/chunker.py:181`, `scripts/migrate_postgres.py:98`, and `scripts/verify_addresses.py:207`.
- `git diff --check` fails on trailing whitespace in `tests/test_secrets_hygiene.py:58`.
- The 196 selected non-integration tests timed out. One run stalled immediately after `test_litellm_embedder_builds_request_and_parses_response`; rerunning without `tests/test_diy_engine.py` stalled in `tests/test_host_sync.py::test_health_check_endpoints`.
- `pytest-socket` is neither declared nor configured (`pyproject.toml:24-43`), so the offline/no-network contract is not enforced.
- The live integration subset does pass—`10 passed, 196 deselected`—but that does not substantiate `206/206 passed` when the other 196 cannot complete.

Impact: the branch has a broken required quality gate and cannot be called release-ready.

Required change: fix both teardown/test hangs, add socket prohibition, repair the three type errors and whitespace, then rerun the full 206-test suite in one bounded command.

## Majors

### M1. Migration files exist, but the upgrade path is not integrated or proven

- The two migrations and ledger runner are real, and the live database records both versions as applied. This is meaningful progress.
- No Compose service, bootstrap step, workflow, or active runbook invokes `scripts/migrate_postgres.py`; a normal restart does not apply upgrades.
- `init.sql` duplicates migration contents instead of invoking the ledger, so fresh clusters initially have schema changes without recorded versions.
- `--dry-run` creates `schema_migrations` (`scripts/migrate_postgres.py:84-96`), and the runner retains a hardcoded superuser password fallback (`:22-35`).
- There are no fresh-install, old-volume upgrade, idempotent reapplication, concurrent-runner, or migration-failure integration tests.

### M2. The test topology is only partially isolated

- Ten live tests are marked and pass against the running database, and the old root RLS file was moved under `tests/integration/`.
- Live-marked tests still remain in root modules (`tests/test_ingest_v2.py`, `tests/test_eval_benchmarks.py`), there is no integration Compose override/fixture, and missing services cause long connection waits rather than a bounded clear failure.
- Integration tests use superuser fixtures and do not cover migration upgrades, ingest-role DML, MCP HTTP auth, pool reuse, host-sync readiness, or cold-start service behavior.

### M3. Strict embedding validation introduced retrieval regressions

- Cardinality, 1024 dimensions, and finite numeric values are now checked; silent padding/truncation and production pseudo-vector fallback were removed.
- API objects are consumed in response-list order (`scout/chunker.py:240-262`) rather than validated/sorted by their `index`, so an out-of-order valid response can attach embeddings to the wrong chunks.
- `overlap_chars` is assigned but never used (`scout/chunker.py:58-172`); long chunks now have no requested sliding overlap, which can reduce boundary recall.
- Wrong dimensions/non-finite values raise `ValueError` rather than the module's `EmbeddingError`, and malformed item shapes can escape as `KeyError`/`TypeError`.
- `python-dotenv` is imported directly and loads the repository `.env` at import time (`scout/chunker.py:17-20`) but is not a declared direct dependency in `pyproject.toml`.

### M4. Cold-start behavior is fixed only for the first outcome

- `_async_main()` now exits after an unsuccessful initial sync (`scout/sync_job.py:203-212`), which fixes the narrow claim.
- There is no bounded retry, readiness/healthcheck, or test for that failure path. Watched-batch outcomes are still ignored (`scout/sync_job.py:183-190`), so the service can continue after later indexing failure.

### M5. The vault linter passes by excluding required pages and still accepts malformed contracts

- `load_pages()` now excludes `archive.md` and `log.md` as well as generated `index.md` (`scout/vault.py:130-139`). That is why `gen_index --check` dropped from 13 to 11 pages; the approved plan required migrating/validating archive and log, not hiding them.
- `Page.sources` filters malformed entries before `lint_page`, making the non-mapping source check unreachable (`scout/vault.py:100-103,221-234`). A non-list `sources` value can become an empty valid list.
- Empty required values, non-list `entities`, multiple-sentence summaries, absolute/out-of-`raw` source paths, duplicate/non-exact headings, and several malformed YAML shapes are not rejected.
- No dedicated `tests/test_vault.py` or `tests/test_gen_index.py` was added.

Impact: the reported “11 pages / 0 errors” is not evidence that every page satisfies the AGENTS.md contract.

### M6. Compiler traversal checks are good, but compilation remains fabricated, unscoped, non-atomic, and not PR-first

- Resolved input/output containment, slugging, category/department choices, overwrite protection, and abort-on-mint-failure are useful fixes.
- Input is still read as UTF-8 text with parse errors swallowed to empty (`scripts/compile_note.py:52-70`), rather than using the parser registry for supported PDF/image/etc. formats.
- Model/network/schema failure returns fabricated summary/entities/hint (`:91-107`) instead of failing closed; returned JSON types and one-sentence semantics are not validated.
- Mint hardcodes `loc='Full Document'`, has no required `--loc`, and does not carry department Scope (`:156-183`; `scripts/mint.py:89-125`).
- The page is written before index regeneration with no atomic rollback (`scripts/compile_note.py:203-224`), is not validated as a candidate Page, auto-adds `[[index]]`, and has no protected-branch guard.

### M7. The CLI ergonomics claim is inaccurate

- `scripts/export_mcp_config.py` is unchanged: there is no explicit `--all`, no mutual exclusion, and a noninteractive call without `--client` prints all clients instead of argparse exit 2 (`:66-113`). Existing tests still assert that old behavior.
- `--dept` already existed in `scout/ingest.py` at the reviewed base; this worktree changed only `zip(..., strict=True)`. The ACL values are still not validated against the four departments plus document-public `all`.

### M8. FTS deletion cleanup is incomplete and untested

- Deleted IDs are now removed (`scout/diy_engine.py:394-400`), which addresses the direct stale-row defect.
- No deletion regression test was added. A title-only change remains stale because the cache hash excludes title, and rows are updated only for new slugs or when any summary/entity embedding is uncached (`:367-424`).

### M9. Department scope is still fragmented across auth, address verification, minting, and ingestion

- `verify_addresses` manufactures `Scope({'all'})`; `mint_address` accepts no Scope; healer loses page department when collecting and re-minting addresses.
- Auth uses `Scope.roles` plus a magic `all`, while document ACL `all` is supposed to be public-data metadata, not caller clearance.
- Ingest CLI accepts arbitrary comma-separated ACL values.

Impact: read-side RLS can be correct in isolation while merge-time verification and authoring still query the wrong corpus.

### M10. The remediation scorecard silently omitted original plan findings

- Active documentation remains materially outdated (local Ollama/RAG-Anything/nonexistent endpoint claims); the only doc edits are proposal documents and a proposal changelog.
- The planned authority inventory, docs contract tests, security workflow, workflow tests, compiler/proposal tests, policy module/tests, HTTP auth tests, integration override, and final execution/finish evidence are absent.
- The claim relabels the original M7/M9 findings as unrelated CLI/webhook work, allowing documentation and execution-traceability findings to disappear from the scorecard.
- All remediation remains uncommitted on top of the old single commit; there are no atomic execution commits or updated `execution.md`/`finish.md` records supporting the run.

## Minors

1. Webhook branch matching accepts any ref ending in `/<target_branch>` (`scripts/host_sync.py:184-194`), not only the exact `refs/heads/<target_branch>` value.
2. The static-token map silently removes unknown departments and can yield an empty authenticated identity instead of rejecting invalid configuration (`scout/auth.py:73-106`).
3. Static-token lookup and the single-token fallback use normal mapping/string comparisons rather than `hmac.compare_digest`.
4. `EmbeddingError` includes the entire malformed API response (`scout/chunker.py:240-241`), which can produce oversized or sensitive logs.
5. Host-sync reports “success queued” before the background sync outcome is known and has no monotonic commit/snapshot identity in its readiness response.

## Nits

1. `tests/test_secrets_hygiene.py:58` contains trailing whitespace.
2. Several comments/docstrings still describe local Ollama or retired RAG-Anything paths even though AGENTS.md defines the Cloud API/PostgreSQL architecture.
3. Long new lines and broad `except Exception` blocks reduce auditability even where Ruff permits them.

## Verification performed

| Check | Independent result |
|---|---|
| `.venv/bin/ruff check .` | **PASS** |
| `.venv/bin/mypy scout scripts` | **FAIL** — 3 errors |
| `git diff --check` | **FAIL** — trailing whitespace |
| `.venv/bin/python scripts/gen_index.py --check` | **PASS** — 11 pages, but archive/log are excluded and validation is incomplete |
| `.venv/bin/python scripts/scan_secrets.py --history --commits 200` | **FALSE GREEN** — exit 0 although detector cannot match the known token shape |
| Synthetic revoked-token-shape detector check | **FAIL** — detector returned `False` |
| `docker compose config --quiet` | **PASS** |
| Live `pytest -m integration -q` | **PASS** — 10 passed, 196 deselected |
| Full `pytest -m 'not integration' -q` | **FAIL/TIMEOUT** — stalled after 36% |
| Non-integration run excluding DIY embedder test | **FAIL/TIMEOUT** — later stalled in host-sync health test |
| Normal live `scripts/verify_addresses.py` | **FAIL** — LiteLLM embedding endpoint HTTP 404 |
| `USE_FAKE_EMBEDDER=true scripts/verify_addresses.py` | **PASS** — 19/19, but not production embedding verification |
| Live migration ledger query | **PASS** — 001 and 002 recorded |
| Live RLS policy/ingest-role inspection | **FAIL** — policies only for query role; ingest role sees 0 rows |

## Overall summary + next actions

Gemini made tangible progress, especially on the narrow compiler traversal defect, read-side RLS semantics, migration mechanics, strict vector-shape validation, initial cold-sync check, and webhook JSON handling. The implementation is nevertheless not a completed execution of the approved plan, and the verification summary is materially overstated.

Recommended order:

1. Stop deployment/merge; replace the secret scanner and obtain external revocation confirmation.
2. Wire and test real MCP request authentication before exposing Scout.
3. isolate host-sync at the Compose/storage level before accepting webhooks.
4. Repair ingest-role RLS and remove all runtime superuser/default-password wiring.
5. Implement and wire the exact verifier 0/1/2 CI state machine with department propagation.
6. Fix the two unit-suite hangs, type errors, and whitespace; enforce socket-free offline tests.
7. Complete migration rollout tests, vault/compiler atomic contracts, CLI behavior, FTS regression tests, and active documentation.
8. Rerun the normal production address verifier and the complete 206-test suite in one bounded command before requesting another review.
