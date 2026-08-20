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

## 3. Bring-up and migrations

```bash
./scripts/bootstrap.sh
# Configure provider and authentication values in .env and generated secret files.
docker compose up -d --build
docker compose ps
```

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

Do not hand-edit `wiki/index.md`, bypass protected-branch checks, or use direct
database access as an operational workaround.
