# SNP Memory System — Operations Runbook

This runbook describes the current Cloud API + PostgreSQL pgvector deployment.
Older topology documents are classified in
[`ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md).

## 1. Trust boundaries

Scout and basic-memory bind to loopback by default. Scout is the only supported
agent path to PostgreSQL RAG; applications do not connect to the database
directly. Source text returned by `rag_fetch` is untrusted evidence and must
never be followed as instructions.

System model calls leave the host through LiteLLM to the configured Cloud API
providers. An agent's own model has a separate data boundary controlled by the
agent client. Do not claim the current stack is offline or no-egress.

Scout authentication modes are:

- `jwt` (default): asymmetric JWT verification with issuer, audience, subject,
  expiry, and department claim validation;
- `static`: opaque bearer tokens mapped to subjects and department sets;
- `development`: no bearer token, accepted only on loopback.

`./scripts/bootstrap.sh` creates `.secrets/scout_static_tokens.json`, and the
primary Compose file mounts it read-only at
`/run/secrets/scout_static_tokens_json`. The map schema is
`{ "<opaque-token>": {"subject": "<server-owned-id>", "departments":
["infra"]} }`. Every subject must be nonempty and every department must be
canonical; `all` is invalid caller authority.

The authenticated departments are `redteam`, `blueteam`, `ai_eng`, and
`infra`. A request can narrow this set but cannot expand it.

### 1.1 Static-token lifecycle

Static tokens **never expire**. `StaticTokenVerifier` returns an `AccessToken`
carrying only the subject, the department set, and the auth mode; it sets no
`expires_at`. Only `jwt` mode validates an expiry claim per request. A leaked
static token therefore stays valid until it is removed from the token map, and
no clock revokes it for you.

The token map is read **once, at Scout start-up**, and the digests are cached
in the verifier for the life of the process. Editing
`.secrets/scout_static_tokens.json` has no effect on a running Scout. Issuing,
rotating, and revoking a static credential are all "edit the file, then restart
Scout" — there is no online revocation path in this mode.

Rotate or revoke with an overlap window, so callers are never locked out
mid-cutover:

```bash
# 1. Add the replacement token beside the current one; both entries stay valid.
$EDITOR .secrets/scout_static_tokens.json
chmod 600 .secrets/scout_static_tokens.json

# 2. Restart Scout to load the new map. Until this step the new token is not
#    accepted, and the old token is still accepted.
docker compose up -d --force-recreate scout

# 3. Move every caller to the new token, delete the old entry, restart again.
#    The old token is revoked only after this second restart.
$EDITOR .secrets/scout_static_tokens.json
docker compose up -d --force-recreate scout
```

For the generated local development identity, use the bootstrap helper rather
than hand-editing the map:

```bash
# Replace every managed local secret, including the token map:
uv run python scripts/bootstrap_secrets.py --rotate
# Or rewrite only the map from the existing .secrets/scout_test_token:
uv run python scripts/bootstrap_secrets.py --refresh-static-token-map
docker compose up -d --force-recreate scout
```

Both write mode `0600`; keep it that way. The map is mounted read-only into
the container at `/run/secrets/scout_static_tokens_json`, so it is edited on
the host, never inside the container.

Choose `static` mode only where "edit a file and restart" is an acceptable
complete credential lifecycle — a single-operator deployment, or integration
testing. Choose `jwt` mode when credentials must expire on their own, be
issued or revoked by an external identity provider, or change without
restarting Scout: it validates issuer, audience, subject, expiry, and the
department claim on every request.

## 2. Services and identities

| Component | Host endpoint | Responsibility |
|---|---|---|
| Gitea | `127.0.0.1:3000` | Git and human PR workflow |
| LiteLLM | `127.0.0.1:4000` | Cloud model/embedding gateway |
| Scout | `127.0.0.1:8080/mcp` | Authenticated `rag_fetch` |
| basic-memory | `127.0.0.1:8765/mcp` | Wiki search/read |
| host-sync | `127.0.0.1:9000` | Signed webhook and replica publisher |
| PostgreSQL | internal; integration override may bind loopback | pgvector/FTS store with RLS |
| sync-job | internal | Raw-file ingestion and deletion reconciliation |
| postgres-migrate | one-shot internal service | Schema migration and role provisioning |

Runtime identities are deliberately separated:

- `rag_app_role`: SELECT through fail-closed RLS for Scout;
- `rag_ingest_role`: DML through explicit ingestion RLS policies;
- migration administrator: schema/role setup only, never a runtime fallback.

Passwords are read from generated files under `.secrets/`; do not put them in
committed configuration or substitute the administrator credential for a
missing runtime secret.

### What CI runs, and what it deliberately does not

Three workflows, in `.gitea/workflows/`:

| Workflow | Trigger | Runs |
| --- | --- | --- |
| `checks.yaml` | PR, push | `ruff check`, `ruff format --check`, `mypy scout scripts`, and the offline test suite (`-m 'not integration'`) |
| `security.yaml` | PR, push | secret scan of the working tree, index, untracked files and all refs, plus an independent Gitleaks history scan |
| `auto-healer.yaml` | PR touching `wiki/**`, weekly schedule | the closed-loop address gate: verify → heal → re-verify → **judge groundedness** → PR-first commit |

**No workflow runs the live verifications**, and that is deliberate:
`verify-addresses` and `verify-groundedness` need PostgreSQL and the LiteLLM
gateway, and the judge runs on a route with a daily request ceiling. A CI job
that fails because a gateway was down teaches people to ignore CI. The live
checks belong to `auto-healer.yaml`, which already requires a self-hosted runner
that can reach both, and to an operator at a terminal.

`checks.yaml` needs neither — no gateway, no database, no secret. It can only
fail on this repository's own code.

### 2.1 Pinned build and workflow inputs

Every input a workflow or an image build executes is pinned to an immutable
reference. `tests/test_supply_chain_pins.py` asserts this, so a new workflow or
Dockerfile cannot quietly reintroduce a floating one.

| Class | Where | Pinned to | Count |
| --- | --- | --- | --- |
| Actions | `.gitea/workflows/*.yaml` | commit SHA (`@<40-hex>`) | 9 |
| Third-party images | `docker-compose.yml` | `@sha256:` digest | 4 |
| Base images | 3 Dockerfiles | `@sha256:` digest | 3 |
| Python dependencies | 3 `*.lock` files | version **and** hash, `--require-hashes` | 246 |
| apt packages | `scripts/Dockerfile.sync` | exact version | 2 |

Resolved values and how each was obtained:
`artifacts/superpowers/supply-chain-pins-2026-08-26.md`.

**Pinning a tag is not the same as pinning a commit.** `astral-sh/setup-uv@v5`
is an *annotated* tag: it resolves to `e58605a9…` as a tag object and
`d4b2f3b6…` as the commit. Both are 40 hex characters and only the second names
the code that runs. Resolve with `GET /repos/{owner}/{repo}/commits/{tag}`,
never `/git/ref/tags/{tag}`.

#### Bumping a pin

```bash
# an action — take the COMMIT, not the tag object
curl -s https://api.github.com/repos/actions/checkout/commits/v4 | jq -r .sha

# a third-party or base image
docker pull <image>:<tag>
docker image inspect <image>:<tag> --format '{{index .RepoDigests 0}}'

# a Python dependency: edit the human-readable requirements file, then
uv pip compile <requirements> --generate-hashes \
    --python-version 3.12 --python-platform linux -o <lock>

# an apt package version, read from a container on the pinned base
docker run --rm <base>@<digest> dpkg-query -W -f='${Package}=${Version}\n' git curl
```

Rebuild and redeploy after any of these. A lock that no longer describes the
running image is worse than no lock, because it reads as evidence.

#### What pinning buys, and what it costs

It removes **silent drift**. Measured on 2026-08-26, before any of this existed:
`pypdf>=4.0.0` had selected **6.16.1** — two major versions above its floor, in
the library that parses every document in the corpus — and `basic-memory==0.22.1`
was one pinned line above a closure of **163** unpinned packages.

It adds **staleness**, and nothing here keeps a pin fresh. A pinned action does
not receive its own security fixes; `pip-audit` in `auto-healer.yaml` covers the
Python side, and the action and image pins are reviewed by hand.

**What is still not pinned, stated so it is not mistaken for covered:**

| Gap | Why |
| --- | --- |
| **apt repository state** | Package *versions* are pinned; the Debian pool is not. Superseded versions are removed, so the build eventually fails to resolve — loudly. `snapshot.debian.org` is the fix, deferred until a build can test it. |
| **`scripts/install-agent.sh`** | Clones the default branch, and its documented `curl … \| bash` install fetches that same moving file. The repository has **0 tags**, so there is nothing to pin to; tag a release first. |
| **The Gitea runner host** | The runner's own configuration and its Docker socket live outside this repository. See the trust-boundary entry in `docs/ARCHITECTURE_STATUS.md`. |

## 3. Bring-up and migrations

### Production release gate

Do not use this bring-up procedure to test an uncommitted worktree or to repair
a corpus built by a different capability environment. Before a production
transition, record the candidate revision, image and lockfile manifest, approved
maintenance/staging target, and PostgreSQL restore point. Run the release
preflight, perform the capability dry run, and use the acknowledgement emitted
by that dry run for the one-shot container-side ingestion. Keep writers stopped
and retain the prior manifest and backup until vault, address, RLS, and
capability checks pass.

The required owner decisions are listed in OD-4 of
`docs/ARCHITECTURE_STATUS.md`. A decision record is not permission to run a
build, restart, re-ingest, push, or runner profile.

### Isolated release-candidate staging

Compose project names isolate networks, volumes, and container names; the
staging overlay also assigns distinct loopback ports. Use a non-default project
name and a disposable **bare** Git remote so host-sync proves its publication
path without reading a checkout directly or touching the production remote.
Docker documents project naming as the mechanism for running isolated copies of
the same Compose application ([Compose project names](https://docs.docker.com/compose/how-tos/project-name/)).

The manifest, capability JSON, backup archive, checksum record, and restore
result must live **outside** the Git worktree. Writing one under `artifacts/`
would make the candidate dirty and correctly cause its preflight to fail.

```bash
# These values are examples for the approved v0.2.1 staging transition.
export SNP_RELEASE_TAG=v0.2.1
export SNP_RELEASE_SHA="$(git rev-parse "${SNP_RELEASE_TAG}^{commit}")"
export SNP_STAGE_PROJECT=snp-v021-staging
export SNP_STAGING_GIT_BRANCH=release-v0.2.1
export SNP_RELEASE_DIR="$(mktemp -d /tmp/snp-v021-release.XXXXXX)"
chmod 700 "$SNP_RELEASE_DIR"

# A disposable, read-only remote whose only branch is the exact tagged source.
# Fetch the peeled commit into the bare remote rather than pushing anywhere.
export SNP_STAGING_SOURCE_REPO="$(mktemp -d /tmp/snp-v021-source.XXXXXX)"
git init --bare "$SNP_STAGING_SOURCE_REPO"
git -c protocol.file.allow=always --git-dir="$SNP_STAGING_SOURCE_REPO" \
  fetch --no-tags "$PWD" \
  "${SNP_RELEASE_SHA}:refs/heads/${SNP_STAGING_GIT_BRANCH}"

# Unique local image names prevent a staging build from overwriting the live
# image tags. All three labels carry the same immutable candidate revision.
export SNP_SCOUT_IMAGE="snp-v021-scout:${SNP_RELEASE_SHA}"
export SNP_BASIC_MEMORY_IMAGE="snp-v021-basic-memory:${SNP_RELEASE_SHA}"
export SNP_HOST_SYNC_IMAGE="snp-v021-host-sync:${SNP_RELEASE_SHA}"
export SNP_GIT_REVISION="$SNP_RELEASE_SHA"
```

Create a portable custom-format archive from the explicitly acknowledged live
source. The helper streams `pg_dump`, runs `pg_restore --list` against the
result, records a SHA-256 checksum, and refuses both repository-local output
and a default-live source unless `--allow-live-source` is spelled out. It omits
object ownership but retains grants and RLS policy ACLs; the restore helper
first runs the staged migration service solely to create its two cluster-scoped
roles and passwords. A logical archive is appropriate for this one-database
staging proof; this stack does not claim point-in-time recovery because it has
no WAL archive. PostgreSQL notes that even a verified base backup still needs a test restore
([pg_verifybackup](https://www.postgresql.org/docs/19/app-pgverifybackup.html)).

```bash
# Backup IDs are deliberately lowercase so the operator confirmation is
# shell-friendly and satisfies the helper's strict identifier contract.
export BACKUP_ID=v0.2.1-prestage-$(date -u '+%Y%m%dt%H%M%S')z
uv run python scripts/release_backup.py create \
  --source-project snp-memory \
  --allow-live-source \
  --backup-id "$BACKUP_ID" \
  --output "$SNP_RELEASE_DIR/${BACKUP_ID}.dump"
```

Build the exact candidate in the isolated project, start PostgreSQL **only**,
and restore the archive into that isolated database. The restore helper rejects
the live default project, requires the exact backup-ID confirmation, invokes
the staged migration service to bootstrap only the cluster-scoped roles, then
drops and recreates only the named staging database, restores transactionally,
and writes a result record. Do not start `sync-job` yet.

```bash
docker compose --project-name "$SNP_STAGE_PROJECT" \
  -f docker-compose.yml -f docker-compose.staging.yml \
  build --pull=false scout basic-memory host-sync
docker compose --project-name "$SNP_STAGE_PROJECT" \
  -f docker-compose.yml -f docker-compose.staging.yml up -d --wait postgres

uv run python scripts/release_backup.py restore \
  --target-project "$SNP_STAGE_PROJECT" \
  --archive "$SNP_RELEASE_DIR/${BACKUP_ID}.dump" \
  --record "$SNP_RELEASE_DIR/${BACKUP_ID}.dump.json" \
  --confirm-backup-id "$BACKUP_ID" \
  --result "$SNP_RELEASE_DIR/${BACKUP_ID}.restore.json" \
  --compose-file docker-compose.yml \
  --compose-file docker-compose.staging.yml

# The bootstrap service completed before restore; the restored migration ledger
# is now the source-of-truth. Start readers, not the automatic writer. Compose
# dependencies observe the successful one-shot service.
docker compose --project-name "$SNP_STAGE_PROJECT" \
  -f docker-compose.yml -f docker-compose.staging.yml \
  up -d --wait postgres-migrate litellm host-sync basic-memory scout

uv run python scripts/preflight_stack.py \
  --compose-project "$SNP_STAGE_PROJECT" \
  --compose-file docker-compose.yml \
  --compose-file docker-compose.staging.yml \
  --image "$SNP_SCOUT_IMAGE"
```

After the candidate images are built, capture their immutable release inventory
before promotion. The capability input must come from the rebuilt parser
container, not from a host that may have different optional extractors. Every
command below names the staging project explicitly; neither helper defaults to
the live project.

```bash
docker compose --project-name "$SNP_STAGE_PROJECT" \
  -f docker-compose.yml -f docker-compose.staging.yml \
  run --rm --no-deps sync-job python -c \
  'import json; from scout.capabilities import capability_fingerprint; print(json.dumps(capability_fingerprint()))' \
  > "$SNP_RELEASE_DIR/container-capability.json"

uv run python scripts/write_release_manifest.py \
  --output "$SNP_RELEASE_DIR/release-manifest.json" \
  --compose-project "$SNP_STAGE_PROJECT" \
  --compose-file docker-compose.yml \
  --compose-file docker-compose.staging.yml \
  --capability-file "$SNP_RELEASE_DIR/container-capability.json"
uv run python scripts/write_release_manifest.py --check \
  --output "$SNP_RELEASE_DIR/release-manifest.json" \
  --compose-project "$SNP_STAGE_PROJECT" \
  --compose-file docker-compose.yml \
  --compose-file docker-compose.staging.yml \
  --capability-file "$SNP_RELEASE_DIR/container-capability.json"

# A candidate may be promoted only when the current evidence still matches its
# immutable manifest. BACKUP_ID is the operator's pre-transition restore point.
uv run python scripts/release_preflight.py \
  --manifest "$SNP_RELEASE_DIR/release-manifest.json" \
  --compose-project "$SNP_STAGE_PROJECT" \
  --compose-file docker-compose.yml \
  --compose-file docker-compose.staging.yml \
  --capability-file "$SNP_RELEASE_DIR/container-capability.json" \
  --backup-id "$BACKUP_ID"
```

`--check` compares the named Compose project **and ordered Compose files**, Git
revision, dirty state, lock hashes, image digests or local OCI labels, migration
ledger, and capability fingerprint. `release_preflight.py` additionally refuses
a missing/mismatched `SNP_GIT_REVISION`, an incomplete migration ledger, or a
blank backup ID. An infrastructure failure while collecting current Docker or
PostgreSQL evidence exits `2`; an unsafe candidate exits `1`; neither condition
permits deployment.

### Rollback drill — required before production use

The following is the restore sequence to exercise in the approved staging
project. It is **not yet a tested-production claim**: record the staging date,
backup identifier, prior manifest path, image digests, verifier results, and
recovery time before changing this status.

1. Stop the writer first (`sync-job`) and prevent any scheduled ingestion from
   starting. Preserve the failed candidate manifest, its container capability
   JSON, and logs.
2. Restore PostgreSQL from the `BACKUP_ID` recorded before the transition,
   using the site's database backup procedure. Do not recreate or delete the
   volume as a shortcut: the backup owner must be able to identify the restore
   point and validate the restored database.
3. Redeploy the exact image digests or local revision-labelled images recorded
   by the previous manifest, with `--no-build`; a source checkout is not a
   rollback artifact. Use the former Compose image mapping retained with that
   manifest, never a fresh build from the previous branch.
4. Capture the restored parser-container fingerprint and run
   `release_preflight.py` against the previous manifest. Reopen Scout and
   `sync-job` only after the old manifest matches and the vault, address, RLS,
   and capability checks pass.

If the drill cannot perform one of those steps, production promotion remains
blocked until the image-retention or backup procedure is repaired.

```bash
./scripts/bootstrap.sh
# Configure provider and authentication values in .env and generated secret files.
SNP_GIT_REVISION=$(git rev-parse HEAD) docker compose up -d --build
docker compose ps
python scripts/preflight_stack.py
```

`--build` matters on every bring-up, not just the first: Scout is the build
owner for the image Scout, `sync-job`, and `postgres-migrate` share; basic-memory
and host-sync each have their own build. A plain `docker compose up -d` requests
none of those builds, so it silently keeps running whatever images were built
last. `SNP_GIT_REVISION` stamps every local build so drift is detectable
afterwards.

Compose starts `postgres-migrate` after PostgreSQL is healthy and holds Scout
and `sync-job` until migration/provisioning succeeds. If the migration service
fails, inspect it before restarting dependants:

```bash
docker compose logs postgres-migrate
docker compose logs scout sync-job
```

The migration runner is forward-only, transactional per file, protected by an
advisory lock, and records filenames in `schema_migrations`. Its check mode has
three exit states: `0` no pending migrations, `1` pending migrations, `2`
configuration/connectivity failure.

```bash
uv run python scripts/migrate_postgres.py --check
```

## 4. Host-sync replica

Host-sync validates an exact `refs/heads/<configured-branch>` webhook ref and
its HMAC signature. It materializes the fetched commit as
`/vault-replica/snapshots/<commit>/wiki`, writes replica metadata, and atomically
repoints `/vault-replica/current`. It never checks out or cleans the developer
repository.

`basic-memory` mounts `/vault-replica/current/wiki` read-only. `/live` confirms
the host-sync process is running; `/ready` succeeds only after a validated
snapshot is published. A failed refresh leaves the last-known-good `current`
snapshot available and readiness reports the failure.

```bash
curl -fsS http://127.0.0.1:9000/live
curl -fsS http://127.0.0.1:9000/ready
```

Treat webhook authentication failure, branch mismatch, malformed JSON, and an
unpublished initial snapshot as deployment failures; do not bypass readiness.

## 5. Verification

Offline deterministic checks:

```bash
timeout 300s uv run pytest -m 'not integration' --disable-socket -q
uv run ruff check .
uv run mypy scout scripts
python scripts/gen_index.py --check
```

Live integration checks use a disposable Compose project:

```bash
docker compose -p snp-memory-it -f docker-compose.yml \
  -f docker-compose.integration.yml up -d --build --wait

export SNP_INTEGRATION_PROJECT=snp-memory-it
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_DB=snp_rag
export POSTGRES_QUERY_USER=rag_app_role
export POSTGRES_QUERY_PASSWORD_FILE="$PWD/.secrets/postgres_query_password"
export POSTGRES_INGEST_USER=rag_ingest_role
export POSTGRES_INGEST_PASSWORD_FILE="$PWD/.secrets/postgres_ingest_password"
export POSTGRES_MIGRATION_USER=postgres
export POSTGRES_MIGRATION_PASSWORD_FILE="$PWD/.secrets/postgres_admin_password"
export LITELLM_BASE_URL=http://127.0.0.1:4000/v1
# Export LITELLM_MASTER_KEY from your secret store; do not paste it into docs.
export SCOUT_INTEGRATION_URL=http://127.0.0.1:8080/mcp
export SCOUT_INTEGRATION_INFRA_TOKEN_FILE="$PWD/.secrets/scout_test_token"
uv run pytest -m integration --force-enable-socket -q
docker compose -p snp-memory-it -f docker-compose.yml \
  -f docker-compose.integration.yml down
```

The integration PostgreSQL binding defaults to `127.0.0.1:55432`. Live tests
fail by naming any missing host prerequisite; they do not skip or use fallback
credentials.

Address verification is live and department-scoped:

```bash
uv run python scripts/verify_addresses.py
```

- `0`: every address is `PASS` (or there are no addresses);
- `1`: semantic `FAIL` or `DRIFT`;
- `2`: backend, model, network, or configuration failure.

The supported CI remediation entry point is
`uv run python scripts/ci_address_gate.py --mode pr`. Exit `2` fails without
mutation. Exit `1` on an eligible branch allows one healer pass, then both
address verification and vault lint run again. Any unsuccessful pass restores
the wiki snapshot. Scheduled mode must start on a protected base and publishes
a `heal/*` branch for human review.

### 5.1 Asking whether the judge is alive

`GET /health?model=snp-judge` **cannot answer this.** It serves the cached
result of LiteLLM's background health loop, and `snp-judge` is deliberately
excluded from that loop — its OpenRouter free key allows 50 requests/day, and
probing every 300s would spend roughly six times that before a single page was
judged. The endpoint therefore reports 503 for this route whether the judge is
up or down. Read that 503 as **unknown**, not unhealthy.

To ask for real, spend one request on purpose:

```bash
uv run python scripts/verify_groundedness.py --probe
# exit 0 — the judge answered
# exit 2 — it did not
```

`scripts/ci_address_gate.py` runs exactly this as a preflight, before anything
can heal. A gate that heals, commits and pushes and *then* discovers its checker
was down has already mutated `sources[]` on the strength of a check that never
happened.

## 6. Common incidents

| Symptom | Response |
|---|---|
| Scout rejects every request | Check auth mode and required issuer/audience/key or static-token file; never switch a non-loopback deployment to development mode. |
| A static token must be revoked now | Delete its entry from `.secrets/scout_static_tokens.json` and restart Scout. Static tokens carry no expiry, so the restart *is* the revocation; waiting does nothing. See 1.1. |
| Caller sees zero rows | Confirm the token's canonical department claim and the page/source department; fail-closed RLS intentionally returns no unauthorized rows. |
| `verify_addresses.py` exits `2` | Repair infrastructure/configuration. Do not run a healer. |
| `verify_addresses.py` exits `1` | Re-mint the address or run the closed-loop CI gate on an eligible feature branch. |
| basic-memory is unavailable | Check `host-sync` `/ready`, its replica metadata, and the published `current` pointer. |
| Initial host sync fails | Fix remote URL, branch, credentials, or webhook secret; there is no last-known-good snapshot on a cold start. |
| Migration check exits `1` | Apply migrations through the migration service before starting runtime services. |
| MCP endpoint returns 401/403 | Supply a valid bearer token and authorized department; a browser GET is not an MCP client. |
| Every model call fails with "Temporary failure in name resolution" | The container's `/etc/resolv.conf` says `NO EXTERNAL NAMESERVERS DEFINED` — Docker captured the host's resolver at container-creation time and it was a loopback one. See 6.1. |
| `sync-job` restarts forever with `error:EmbeddingError` | Two known causes, in this order: the DNS fault above, and a stale `snp-scout` image. See 6.1 and 6.2. |
| Ingest 400s with `at most 100 requests can be in one batch` | The running image predates the batch-splitting cap. Rebuild it — see 6.2. |

Do not hand-edit `wiki/index.md`, bypass protected-branch checks, or use direct
database access as an operational workaround.

### 6.1 Containers cannot resolve external hostnames

Containers resolve through Docker's embedded server at `127.0.0.11`, which
forwards to the nameservers Docker copied from the host's `/etc/resolv.conf`
**when the container was created**. If that file named only a loopback resolver
at that moment (`systemd-resolved` on `127.0.0.53`, dnsmasq, a VPN client), the
embedded server has no upstream and every outbound provider call fails. The
symptom appears far from the cause: a `500` from the gateway, or
`error:EmbeddingError` in `sync-job`.

Confirm it:

```bash
docker compose exec -T litellm cat /etc/resolv.conf
# a broken container says:  # NO EXTERNAL NAMESERVERS DEFINED
# a working one says:       # ExtServers: [host(10.0.0.53) ...]
```

Fix, in order of preference:

1. **The host has real nameservers now, the container holds a stale copy** —
   the common case after a network change. Recreate just the affected services:

   ```bash
   docker compose up -d --force-recreate litellm scout sync-job
   ```

   Name the services explicitly. A bare `--force-recreate` also recreates
   `postgres`, which holds the corpus.

2. **The host genuinely cannot supply routable nameservers** — state the
   resolver explicitly with the opt-in override:

   ```bash
   SNP_DNS_SERVERS=10.0.0.53 \
     docker compose -f docker-compose.yml -f docker-compose.dns.yml up -d
   ```

   Use your site's resolver. Do not substitute a public one: on a split-horizon
   network it cannot see internal names, and internal hostnames would be sent to
   a third party.

3. **Host-wide, by the machine's administrator** — `{"dns": [...]}` in
   `/etc/docker/daemon.json`. More permanent, but global to the machine and it
   changes every unrelated Docker workload on it, so it is not shipped here.

Because Docker's restart backoff resets after a container survives 10 seconds,
a service that fails *slowly* (DNS timeouts are slow) never accumulates any
backoff — it simply restarts forever at a near-constant rate. Do not read a
climbing `RestartCount` as evidence that Docker is throttling anything.

### 6.2 The running image is older than the checkout

Scout, `sync-job`, and `postgres-migrate` share `${SNP_SCOUT_IMAGE:-snp-scout}`;
Scout is their build owner. basic-memory and host-sync have separate local
images, but a plain `docker compose up` **never requests any build**. An image
built before a fix keeps running after the fix is committed, and the failure it
causes looks like a live bug rather than a deployment fault.

Confirm it:

```bash
docker image inspect snp-scout --format '{{.Created}}'
git log -1 --format=%cI
```

If the image predates the checkout, rebuild and recreate:

```bash
SNP_GIT_REVISION=$(git rev-parse HEAD) docker compose build scout
docker compose up -d --force-recreate scout sync-job
```

Passing `SNP_GIT_REVISION` stamps `org.opencontainers.image.revision` into the
image, which is what turns the next check from a timestamp guess into a fact.
Building without it leaves the label `unknown`, and the preflight then reports
the image as *unverifiable* rather than healthy — deliberately, because a build
timestamp cannot tell a clean build from one made off a dirty tree.

Both checks are one command:

```bash
python scripts/preflight_stack.py
# For isolated staging, name both its Compose project and its unique Scout tag:
# python scripts/preflight_stack.py --compose-project snp-v021-staging \
#   --compose-file docker-compose.yml --compose-file docker-compose.staging.yml \
#   --image "${SNP_SCOUT_IMAGE}"
```

Exit `0` both pass · `1` a check failed, with the fix printed · `2` docker or
git could not be reached, which is never a clean result.

This matters beyond ingestion: `scout` is the only door into RAG, so a stale
image can be serving retrieval under an older authorization model than the one
in the repository. Rebuild before trusting any live verification result.
