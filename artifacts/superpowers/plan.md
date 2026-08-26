## Goal

Deliver a staged, production-ready SNP Memory System by closing the twelve active gaps without corrupting the current corpus or treating static checks as proof of a working release.

The plan applies current secure-delivery and retrieval practices: immutable artifact promotion, a release manifest/SBOM-equivalent inventory, least-privilege operational testing, versioned multilingual retrieval evaluation, and isolated resource-bounded processing of untrusted files.

## Assumptions

- This is a planning-only artifact. The current commit/push/rebuild/re-ingest hold remains in force until the owner explicitly lifts it.
- The clean release target is the reviewed, committed form of the current worktree; all later runtime evidence must name that exact Git revision and image digest.
- Production release work must use a maintenance window or an isolated staging Compose project with a database backup/restore point before any container-side re-ingest.
- The owner must decide: supported languages; release version; article/judge budget; derived-asset retention/location; source-health thresholds and quarantine policy; Office-format scope; `BOT_TOKEN` scope; and runner-host Docker-socket posture.
- The researched practices incorporated here are immutable action/image references and artifact promotion ([GitHub Actions hardening](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-your-deployments#using-third-party-actions), [Docker image pinning](https://docs.docker.com/build/building/best-practices/#pin-base-image-versions)); bounded hostile-file processing ([OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)); and versioned retrieval metrics such as Recall@k, MRR, and context precision/recall ([Ragas metrics](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)).
- Native MCP Tasks remain a deliberately deferred capability unless Redis or a durable dependency-free backend becomes justified.

## Plan

1. **Record the owner decisions and release gate**
   - Files: `docs/ARCHITECTURE_STATUS.md`, `docs/REMAINING_TASKS.md`, `docs/runbook.md`
   - Change:
     - Add one dated production-readiness decision record naming the release SHA, release version, maintenance/staging choice, backup owner, multilingual decision deadline, article budget, asset policy deadline, and source-health policy deadline.
     - Keep unapproved decisions as explicit gates; do not replace them with defaults.
   - Verify: `rg -n "production-readiness|release SHA|owner decision" docs/ARCHITECTURE_STATUS.md docs/REMAINING_TASKS.md docs/runbook.md`; `uv run pytest tests/test_docs_contract.py -q`.

2. **Add a machine-checkable release manifest**
   - Files: `scripts/write_release_manifest.py` (new), `tests/test_release_manifest.py` (new), `docs/runbook.md`
   - Change:
     - Generate a JSON manifest containing Git revision, dirty-tree state, Compose image digests, local-image labels, lockfile SHA-256 values, migration ledger version, parser capability fingerprint, and timestamp.
     - Treat this as the release inventory/SBOM-equivalent input; it records what was tested and promoted without claiming that it proves source integrity.
   - Verify: `uv run pytest tests/test_release_manifest.py -q`; `uv run python scripts/write_release_manifest.py --check`.

3. **Create the release preflight and rollback runbook**
   - Files: `scripts/release_preflight.py` (new), `tests/test_release_preflight.py` (new), `docs/runbook.md`
   - Change:
     - Fail before deployment when the worktree is dirty, `SNP_GIT_REVISION` is absent/mismatched, locks/digests are unverified, migrations are pending, or a backup/restore-point identifier is missing.
     - Document a tested restore sequence: stop writers, restore PostgreSQL, restore the previous image digest, and verify the old manifest before reopening ingestion.
   - Verify: `uv run pytest tests/test_release_preflight.py -q`; `uv run python scripts/release_preflight.py --help`.

4. **Freeze and review the release candidate**
   - Files: release manifest produced under `artifacts/releases/`, Git tag metadata, `docs/runbook.md`
   - Change:
     - After the hold is lifted, split functional changes from formatting where practical, run the complete review, commit the approved candidate, and create an annotated candidate tag.
     - Capture the pre-release database backup and manifest before starting any new image.
   - Verify: `git status --short` is empty; `git diff --check`; `uv run pytest -m 'not integration' --disable-socket -q`; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy scout scripts`; `git show --no-patch --format=fuller <candidate-tag>`.

5. **Build immutable local images in staging or the approved maintenance window**
   - Files: `docker-compose.yml`, `scripts/preflight_stack.py`, release manifest output
   - Change:
     - Build `scout`, `basic-memory`, and `host-sync` from the candidate revision with the locked dependencies and pinned base images; record their resulting digests in the manifest.
     - Pass `SNP_GIT_REVISION` into every locally built Dockerfile and add the
       same `org.opencontainers.image.revision` OCI label to `basic-memory` and
       `host-sync` that Scout already carries; a local image with neither digest
       nor revision label is intentionally rejected by the manifest.
     - Promote the exact tested images to production; never rebuild from the same source after testing.
   - Verify: `SNP_GIT_REVISION=$(git rev-parse HEAD) docker compose build --pull=false scout basic-memory host-sync`; `docker compose config`; `uv run python scripts/preflight_stack.py`.

6. **Perform a controlled container-side corpus transition**
   - Files: `docs/runbook.md`, `scripts/release_preflight.py`, `tests/integration/test_postgres_migrations.py`
   - Change:
     - Start migrations before runtime services, hold automatic writers until the new container capability fingerprint is recorded, then run the approved one-shot ingest with the exact capability acknowledgement captured from its dry run.
     - Do not use a broad restart as a substitute for this transition; preserve the backup and old manifest until address, RLS, and corpus checks pass.
   - Verify: `docker compose up -d postgres postgres-migrate litellm`; `docker compose ps`; approved one-shot container ingest; `uv run python scripts/preflight_stack.py`; `snpmemory verify-vault`; `snpmemory verify-addresses`; `SNP_INTEGRATION_PROJECT=<approved-project> uv run pytest -m integration tests/integration/test_postgres_migrations.py tests/integration/test_postgres_rls.py -q`.

7. **Record and gate the container parser contract**
   - Files: `tests/fixtures/parser-golden/` (new container fingerprint entry), `tests/test_parser_golden.py`, `docs/REMAINING_TASKS.md`
   - Change:
     - Record the parsed-structure golden from the rebuilt container against its real capability fingerprint; retain the host golden as a separate environment.
     - Make release CI reject an unrecognised canonical container fingerprint.
   - Verify: container parser-golden recorder command; `SNP_CANONICAL_ENV=1 uv run pytest tests/test_parser_golden.py -q`.

8. **Publish the first reproducible release and pin the installer**
   - Files: `scripts/install-agent.sh`, `docs/CONNECT_AGENTS.md`, `docs/runbook.md`, `docs/REMAINING_TASKS.md`
   - Change:
     - Convert the candidate tag to the approved release tag, make it the installer default, and fetch the curl installer at the tag rather than `main`.
     - Require the installer to print both tag and resolved commit.
   - Verify: `git tag --list`; `SNP_AGENT_REF=<release-tag> ./scripts/install-agent.sh --dry-run <temp-dir>`; `uv run pytest tests/test_cli_install_agent.py tests/test_agent_package.py -q`.

9. **Build an isolated real basic-memory publication fixture**
   - Files: `tests/integration/conftest.py`, `tests/integration/test_basic_memory_mcp_live.py` (new), `docs/CONNECT_AGENTS.md`
   - Change:
     - Use a disposable Git remote, vault-replica volume, and Compose project; publish a known page through host-sync rather than reading a checkout directly.
     - Keep all test data outside the production Git remote and PostgreSQL corpus.
   - Verify: `SNP_INTEGRATION_PROJECT=<isolated-project> uv run pytest -m integration tests/integration/test_basic_memory_mcp_live.py -q`.

10. **Assert the real first-hop contract**
    - Files: `tests/integration/test_basic_memory_mcp_live.py`, `tests/fixtures/wiki-search-eval/first_hop.jsonl` (new)
    - Change:
      - Call deployed `search_notes` using high-specificity fixture queries, assert the target page is returned within a documented K, then call `read_note` using the returned identifier and assert title/body/frontmatter.
      - Test unavailable host-sync/basic-memory as infrastructure failure, never as an empty search result.
    - Verify: same isolated integration command; intentionally stop basic-memory and assert the test reports the expected infrastructure failure.

11. **Create a versioned multilingual retrieval evaluation set**
    - Files: `tests/fixtures/wiki-search-eval/queries.jsonl` (new), `scripts/eval_wiki_search.py` (new), `tests/test_eval_wiki_search.py` (new), `docs/basic-memory-setup.md`
    - Change:
      - Add human-reviewed English and Vietnamese queries with expected page IDs and intent labels; report Recall@1/3/5, MRR, and failures by language.
      - Record the current English-only baseline; fixtures are versioned and must be extended whenever the corpus grows.
    - Verify: `uv run pytest tests/test_eval_wiki_search.py -q`; `SNP_INTEGRATION_PROJECT=<isolated-project> uv run python scripts/eval_wiki_search.py --output artifacts/evals/wiki-search-<tag>.json`.

12. **Make and prove the wiki-search language decision**
    - Files: `basic-memory/config.json`, `basic-memory/Dockerfile`, `docs/basic-memory-setup.md`, `docs/ARCHITECTURE_STATUS.md`, evaluation fixtures/results
    - Change:
      - If multilingual support is accepted, conduct a container compatibility spike for candidate FastEmbed models, choose only a model that passes the versioned evaluation without unacceptable English regression, then reindex a staging replica.
      - If English-only is accepted, state the audience restriction and preserve the baseline as an explicit accepted limitation.
    - Verify: staging reindex; evaluation command from step 11; a regression test enforcing the owner-selected threshold and language contract.

13. **Expand and repair the curated corpus under budget control**
    - Files: `artifacts/plans/computers-12-00091.recommended.json`, `wiki/concepts/*.md`, `wiki/index.md` (generated), `artifacts/evals/`
    - Change:
      - Authorise the six-article batch budget, compile resumably, re-mint the known bad hint, and recompile legacy pages that need `Works Cited`.
      - Define a release corpus target by independent sources and query coverage, not page count alone.
    - Verify: `snpmemory compile-plan artifacts/plans/computers-12-00091.recommended.json --confirm --background`; `snpmemory compile-status <handle>`; `snpmemory verify-vault`; `snpmemory verify-addresses`; evaluation command from step 11.

14. **Define source-health policy before implementing enforcement**
    - Files: `docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md`, `docs/ARCHITECTURE_STATUS.md`, `docs/REMAINING_TASKS.md`
    - Change:
      - Record type-specific yield thresholds, report-versus-quarantine policy for insufficient sources, supported Office-format scope, validation location, retention period, and the human approval path for restore/override.
      - Define a state machine: `healthy`, `insufficient`, `unavailable`, `quarantined`, and `replaced`; only `quarantined` suppresses retrieval.
    - Verify: decision table is complete; `uv run pytest tests/test_docs_contract.py -q`.

15. **Persist source-health state and immutable events**
    - Files: `config/postgres/migrations/006_source_health.sql` (new), `scout/source_health.py` (new), `scout/ingest.py`, `tests/integration/test_source_health_rls.py` (new)
    - Change:
      - Add a current-state record keyed by source URI plus content hash/validator revision, and an append-only health-event table with separate least-privilege grants.
      - Store outcomes even for zero-chunk parses; never infer health from the continued existence of old chunks.
    - Verify: migration ledger test; integration tests prove the health writer can insert/select only and cannot alter prior events.

16. **Hash-gate validation and use the ingestion parser as the sole parser**
    - Files: `scout/source_health.py`, `scout/ingest.py`, `scout/sync_job.py`, `tests/test_source_health.py` (new)
    - Change:
      - Compute file SHA-256 streaming from disk; skip expensive revalidation only when content hash and validator revision both match.
      - Call `parse_file` through one shared interface for ingest and health reporting; do not add a second parser.
    - Verify: unit tests for unchanged skip, changed healthy re-ingest, changed unhealthy quarantine candidate, and validator-revision sweep; test a multi-gigabyte synthetic stream without loading it fully into memory.

17. **Isolate hostile parsing before enabling broad health scans**
    - Files: `scout/parser_worker.py` (new), `scout/source_health.py`, `scout/parsers.py`, `docker-compose.yml`, `tests/test_parser_worker.py` (new)
    - Change:
      - Run format parsing in a short-lived, non-root worker with no general network access, file-size and decompression-ratio caps, XML entity/DTD denial, CPU/wall-clock timeout, memory ceiling, and capped concurrency.
      - Keep optional model validation in a separately rate-limited gateway-only path; it must not grant raw parser workers model credentials.
    - Verify: tests for timeout, ZIP bomb ratio, unsafe XML, malformed PDF, and concurrency cap; Compose/service configuration proves the parser worker has read-only raw access and no Docker socket or database-admin credential.

18. **Expose health results without deleting evidence**
    - Files: `scout/types.py`, `scout/core.py`, `scout/backends/pgvector.py`, `scripts/verify_addresses.py`, `scout/cli/commands/verify.py`, `tests/test_core.py`, `tests/test_verify_addresses.py`
    - Change:
      - Add `FetchStatus.SOURCE_QUARANTINED`; make addressed fetches return it distinctly, without context, when the source is quarantined.
      - Preserve `NO_EVIDENCE` for address verification, route operators to source remediation, and prevent healer/mint from treating source failure as a hint problem.
    - Verify: unit and integration tests for healthy, no-source, no-evidence, and quarantined states; ensure no path returns stale chunks from quarantined evidence.

19. **Add operator reporting, reversible quarantine, and controlled restoration**
    - Files: `scout/cli/commands/source_health.py` (new), `scout/cli/declarations.py`, `scout/cli/mcp_policy.py`, `scripts/propose_page.py`, `docs/runbook.md`, tests for the new command
    - Change:
      - Provide a read-only report by default; require a reason, explicit confirmation, and PR-backed review to quarantine, override, or restore.
      - Keep original raw bytes and prior chunks for audit/rollback, but exclude quarantined sources from serving and verification success.
    - Verify: CLI schema/output/exit-code tests; integration test proving a false-positive override restores service only after authorised review; runbook dry-run walkthrough.

20. **Add a hash-gated scheduled health sweep only after the safe path works**
    - Files: `scout/sync_job.py`, `docker-compose.yml`, `.gitea/workflows/auto-healer.yaml` or a dedicated health workflow, `tests/test_sync_job.py`
    - Change:
      - Schedule only the free hash/metadata scan by default; queue paid/model validation for changed or stale-validator sources with bounded rate and concurrency.
      - Emit alerts/reports rather than automatically repairing raw evidence.
    - Verify: unchanged corpus produces no parser/model calls; changed source produces one health event; simulated gateway budget exhaustion leaves data intact and readiness honest.

21. **Design and implement the derived-asset boundary**
    - Files: `docs/ARCHITECTURE_STATUS.md`, `docs/runbook.md`, `docker-compose.yml`, `config/postgres/migrations/007_derived_assets.sql` (new), `scout/derived_assets.py` (new), tests
    - Change:
      - After the owner chooses location and retention, store derived assets by content hash with source URI, source hash, ACL, extractor/version, and retention metadata.
      - Enforce ACL at read time as well as copy time; deleting an asset must never delete the raw source or source-health history.
    - Verify: migration/RLS tests; asset metadata contract tests; cross-department denial test; retention dry run.

22. **Implement `snpmemory extract` behind the derived-asset contract**
    - Files: `scout/cli/commands/extract.py` (new), `scout/cli/declarations.py`, `scout/cli/mcp_policy.py`, `scout/pdf_structure.py`, `tests/test_cli_extract.py` (new), `tests/test_derived_assets.py` (new), `docs/CLI_SPEC.md`
    - Change:
      - Extract only supported, health-cleared source types using the isolated worker; persist assets and metadata atomically with ACL inheritance.
      - Return unavailable/insufficient conditions honestly; do not claim a zero count when the extractor was unavailable.
    - Verify: command schema test; path-boundary, ACL, idempotency, failure-rollback, and capability-unavailable tests; container integration test after enabling the chosen extractor.

23. **Treat Office ingestion and advanced layout extraction as a separate gated epic**
    - Files: `docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md`, `docs/ARCHITECTURE_STATUS.md`, a new spike under `spikes/office-ingest/` only if approved
    - Change:
      - Keep DOCX/XLSX unsupported until an isolated parser/evaluation spike passes the controls from step 17 and has type-specific health thresholds.
      - Evaluate a layout-aware extractor only against a representative corpus and resource budget; do not introduce it through `extract` opportunistically.
    - Verify: scope remains prohibited in active guidance until the spike has a signed-off result; approved spike has correctness, security, and cost evidence.

24. **Generate agent CLI reference from the registry**
    - Files: `scripts/generate_agent_cli_reference.py` (new), `packages/snp-agent/instructions/cli-reference.md` (generated), `.agent/instructions/cli-reference.md` (generated), concise hand-written guidance file, `tests/test_agent_cli_reference.py` (new)
    - Change:
      - Generate command names, arguments, effect, output fields, and exit codes from `snpmemory schema`; do not copy a manually maintained list.
      - Add a small static guide for wiki-first retrieval, minted hints, PR-first changes, and semantic versus infrastructure exits.
    - Verify: generator is byte-stable; `snpmemory schema` matches both package surfaces; package equivalence tests pass.

25. **Keep native MCP Tasks as an evidence-backed deferral**
    - Files: `docs/ARCHITECTURE_STATUS.md`, `docs/REMAINING_TASKS.md`, `tests/test_local_mcp_server.py`
    - Change:
      - Record concrete revisit triggers: Redis adopted for another approved service, a durable dependency-free task backend becomes available, or the MCP client estate requires the standard protocol.
      - Preserve the on-disk handle/heartbeat/cancel protocol and add a dated review reminder rather than introducing Redis solely for parity.
    - Verify: local MCP capability advertises no Tasks without its backend; the existing persistent-handle tests remain green; the decision record has an owner and review date.

26. **Reconcile active documentation and execute the final production review**
    - Files: `docs/REMAINING_TASKS.md`, `docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md`, `docs/Ideas.md`, `docs/ARCHITECTURE_STATUS.md`, `README.md`, `artifacts/superpowers/finish-production-readiness.md` (new)
    - Change:
      - Reconcile partial-versus-unimplemented source-health wording, retire stale Idea counts, and mark only verified release outcomes complete.
      - Perform a final review against the release manifest, rollback drill, user-path test, language evaluation, source-health tests, asset ACL tests, and runner exercise.
    - Verify: `uv run pytest -m 'not integration' --disable-socket -q`; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy scout scripts`; targeted integration suites; `git diff --check`; no stale-claim match in active documents.

## Risks & mitigations

- **Corpus loss or downgrade during release:** require an immutable database backup, old release manifest, stopped writers, a dry-run capability mismatch, and one-shot controlled ingest—not a broad restart.
- **A build passes static checks but fails at runtime:** build and test the exact image digests in staging, then promote those exact images; never rebuild after test.
- **Multilingual change degrades current English behavior:** use versioned bilingual fixtures and published thresholds; reject model changes without measured improvement or explicit owner acceptance.
- **Quarantine causes evidence loss:** preserve raw bytes, chunks, current state, and append-only history; suppress serving rather than deleting; require reviewed restoration.
- **Parser sandbox escape or resource exhaustion:** isolate workers, remove credentials/network, bound file type/size/time/memory/decompression/concurrency, and fuzz hostile fixtures before enabling uploads.
- **Derived asset ACL leak:** enforce ACL at both materialization and read; test cross-department denial and source-ACL changes.
- **Runner compromise:** exercise it only on an approved runner host; inspect/split bot tokens; retain the Docker-socket decision as explicit host risk.
- **Scope overload:** each numbered phase is separately releasable; R0 and R1 are prerequisites, while R2–R4 can be approved independently after their owner decisions.

## Rollback plan

- **R0 release:** stop writers, restore the database backup, redeploy the previous image digests from the prior manifest, run `preflight_stack.py`, then re-enable sync only after verification.
- **Search model:** retain the previous basic-memory index/model configuration until bilingual evaluation passes; roll back the config and index atomically.
- **Corpus compilation:** keep generated pages staged on a feature branch; revert the PR or restore prior pages/index from Git.
- **Source health:** migrations are additive; disable the scheduler and set sources to report-only while preserving events/chunks. Roll back serving policy before deleting no data.
- **Derived assets:** disable extraction/read exposure and retain metadata/assets until retention policy permits removal; raw sources and source-health records are untouched.
- **Runner/installer:** disable the runner profile or revoke the bot token; revert installer default to the last signed release tag, never to an unreviewed branch.
