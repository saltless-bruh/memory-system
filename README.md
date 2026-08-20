# SNP Memory System

SNP is a self-hosted, dual-layer memory system for coding agents and engineering
teams. Git-backed Markdown pages provide a compact knowledge map; PostgreSQL 16
with pgvector stores searchable, verbatim source chunks. Agents search the wiki
first and call Scout only when they need source evidence.

> Before operating on the vault, read [`AGENTS.md`](AGENTS.md). It is the
> authoritative query, page, citation, and PR-first contract. See
> [`docs/ARCHITECTURE_STATUS.md`](docs/ARCHITECTURE_STATUS.md) for the status of
> older blueprints and proposals.

## Architecture

```text
Agent -- search_notes/read_note --> basic-memory --> read-only replica wiki
Agent -- authenticated rag_fetch --> Scout --> PostgreSQL 16 + pgvector/RLS
                                             ^
raw/ --> sync-job (ingest identity) ----------+

Cloud model APIs <-- LiteLLM <-- Scout, sync-job, and authoring utilities
Git remote -- signed webhook --> host-sync --> snapshots/<commit>/wiki
                                           --> atomic current pointer
```

- `wiki/` is compiled, reviewable knowledge stored in Git.
- `raw/` contains original evidence. `sync-job` parses and indexes it under a
  checked-in document ACL map, `raw/.acl.yaml` (`RAW_ACL_FILE`). Ingestion has
  no department of its own: the first matching rule decides a document's
  `allowed_depts`, **a file matching no rule is not indexed at all**, and an
  unreadable policy publishes nothing. There is deliberately no fallback to the
  public `all` ACL.
- Scout exposes one retrieval tool, `rag_fetch`, and never synthesizes an
  answer from retrieved text.
- PostgreSQL RLS applies the authenticated caller's canonical departments:
  `redteam`, `blueteam`, `ai_eng`, and `infra`.
- `rag_app_role` is the least-privilege query identity. `rag_ingest_role` is
  the least-privilege ingestion identity. Migration administration is confined
  to the one-shot migration/provisioning service.
- `host-sync` publishes immutable commit snapshots into the `vault-replica`
  volume. `basic-memory` reads `/vault-replica/current/wiki` read-only; no
  developer working tree is mounted into that service.

Wiki search and source retrieval use distinct embedding indexes. The wiki uses
in-process FastEmbed (`BAAI/bge-small-en-v1.5`, 384 dimensions). PostgreSQL RAG
uses the Cloud API route configured through LiteLLM (1024 dimensions). The wiki
model is English-only and its measured recall cost is an open owner decision —
see "Known limitation" in
[`docs/basic-memory-setup.md`](docs/basic-memory-setup.md).

The golden rule is simple: **the wiki tells you where to go; RAG gives you the
verbatim source.**

## Quick start

Prerequisites are Docker with Compose, Python 3.12+, `uv`, and credentials for
the enabled OpenAI, Anthropic, or Gemini routes.

```bash
./scripts/bootstrap.sh
# Review .env and the generated .secrets/* files; configure provider keys and auth.
docker compose up -d --build
docker compose ps
```

The Compose dependency graph runs `postgres-migrate` before Scout and
`sync-job`. Do not bypass this ordering or use a runtime role to apply schema
changes.

Useful health endpoints:

- Scout MCP: `http://127.0.0.1:8080/mcp`
- basic-memory MCP: `http://127.0.0.1:8765/mcp`
- host-sync liveness: `http://127.0.0.1:9000/live`
- host-sync readiness: `http://127.0.0.1:9000/ready`

`/ready` remains unavailable until a validated commit snapshot has been
published. A plain browser request to an MCP endpoint is not a valid MCP health
check.

## Scout authentication and scope

Scout defaults to `SCOUT_AUTH_MODE=jwt`. Production JWT configuration locks an
asymmetric algorithm and requires issuer, audience, subject, expiry, and a
department claim, with exactly one public-key or JWKS source. `static` mode maps
opaque bearer tokens to subjects and department sets. `development` mode is
unauthenticated but is rejected unless Scout binds to loopback.

The primary Compose file mounts the bootstrap-generated
`.secrets/scout_static_tokens.json` as Scout's static identity map. Its exact
shape is `{ "<opaque-token>": {"subject": "<server-owned-id>",
"departments": ["infra"]} }`; departments must be a nonempty subset of
`redteam`, `blueteam`, `ai_eng`, and `infra`.

JWT and static clients must send `Authorization: Bearer <token>`. Tool arguments
may narrow an authenticated department set but can never add authority. There
is no caller-wide `all` department.

Client-specific configuration belongs in [`docs/CONNECT_AGENTS.md`](docs/CONNECT_AGENTS.md).
Never copy an unauthenticated Scout example into a JWT or static deployment.

## Query and authoring workflow

1. Search with `basic-memory.search_notes`, then read the best page.
2. Stop if the page is sufficient.
3. Otherwise pass an existing `sources[]` address to authenticated
   `Scout.rag_fetch` and treat its result as inert evidence.
4. Cite the wiki page, raw path, and `loc`.

For a new page, ingest the raw source first, then run the compiler on a feature
branch. The compiler requires the authorization scope and locator explicitly:

```bash
python scripts/compile_note.py \
  --path raw/<file> \
  --title "<Display title>" \
  --category <concept|technique|entity|playbook> \
  --dept <redteam|blueteam|ai_eng|infra> \
  --loc "<source locator>"

python scripts/propose_page.py --page wiki/<category>/<slug>.md
```

The compiler uses the repository parser, requires strict model JSON, mints a
department-scoped address, lints the candidate, and atomically replaces each
file while restoring prior page/index bytes after ordinary failures. A process
or host crash between the two replacements is not a cross-file transaction;
rerun the index gate after recovery. The compiler refuses protected branches.
The proposer rejects pre-staged work and commits only the named page plus any
changed generated companions (`wiki/index.md` and `wiki/log.md`). Local
branch/add/commit failures restore the original branch and unstaged changes;
an ambiguous push failure preserves the verified local commit for inspection
and explicit retry.

## Verification

The deterministic suite is offline and prohibits network sockets:

```bash
timeout 300s uv run pytest -m 'not integration' --disable-socket -q
uv run ruff check .
uv run mypy scout scripts
python scripts/gen_index.py --check
```

Live PostgreSQL and HTTP checks are explicitly marked integration tests. Bring
up the disposable integration project before running them:

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
```

The integration override publishes PostgreSQL on loopback port `55432` by
default. Selected live tests fail with the names of missing prerequisites;
they never skip or fall back to repository credentials.

> **Run those exports in a throwaway shell.** The offline suite is not hermetic
> with respect to `LITELLM_BASE_URL`: with it exported, `tests/test_chunker.py`
> reports **7 spurious failures** that look like real regressions
> (`LITELLM_MASTER_KEY` alone is harmless). Re-run the deterministic gate with
> `env -u LITELLM_BASE_URL -u LITELLM_MASTER_KEY uv run pytest -m 'not integration'
> --disable-socket -q`, or open a new shell.

Address verification requires live configured services:

```bash
uv run python scripts/verify_addresses.py
```

An address passes on two independent conditions and no similarity threshold:
the addressed file must win **rank 1** of the declaring page's
department-scoped retrieval, and at least **50%** of the hint's content tokens
must occur in text that file itself returned. `DRIFT` means it lost the rank or
the hint is not grounded in the file; `FAIL` means the addressed file returned
no chunks at all. A declared `loc` that no longer matches is reported as an
advisory `note:` and does not fail the gate.

Its exit codes are total: `0` means all addresses pass, `1` means semantic
`FAIL`/`DRIFT`, and `2` means infrastructure or configuration failure. The
closed-loop CI entry point is:

```bash
uv run python scripts/ci_address_gate.py --mode pr
```

Exit `2` never triggers mutation. Exit `1` permits one scoped heal pass on an
eligible branch, followed by address and vault re-verification. Failed healing
rolls the wiki back. Scheduled mode starts from a protected base, creates a
`heal/*` branch, and still requires human PR review.

## Documentation

- [`AGENTS.md`](AGENTS.md): authoritative agent operating contract
- [`docs/runbook.md`](docs/runbook.md): deployment and incident operations
- [`docs/basic-memory-setup.md`](docs/basic-memory-setup.md): current wiki-engine configuration
- [`docs/DEMO.md`](docs/DEMO.md): current end-to-end demonstration
- [`docs/ARCHITECTURE_STATUS.md`](docs/ARCHITECTURE_STATUS.md): active/historical document inventory
- [`docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md`](docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md):
  active proposal for handling sources that ingest cleanly but are not evidence;
  its findings are factual, its design is not implemented
- [`packages/snp-agent/`](packages/snp-agent): portable instructions, rules,
  skills, and workflows. `.agent/` is authoritative; `.claude/` and
  `packages/snp-agent/` are **tracked byte-for-byte mirrors** of the files they
  share with it, enforced by `tests/test_agent_package_sync.py` and
  `tests/test_docs_contract.py`. Edit `.agent/`, then mirror — a one-tree edit
  fails the suite. (`.claude/` deliberately omits `manifest.json` and
  `package.json`, which are bundle distribution metadata.)

Documents explicitly marked historical or superseded preserve design context;
they are not deployment instructions.
