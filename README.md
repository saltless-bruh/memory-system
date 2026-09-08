# SNP Memory System

SNP is a self-hosted memory system for coding agents and engineering teams.
Git-backed Markdown pages provide a compact, compiled knowledge map; the same
PostgreSQL 16 with pgvector index also stores the verbatim source chunks those
pages were compiled from. Agents call the authenticated Scout server for both:
`wiki_search` to find candidate pages, then `wiki_read` to read one.

> Before operating on the vault, read [`AGENTS.md`](AGENTS.md). It is the
> authoritative query, page, citation, and PR-first contract. See
> [`docs/ARCHITECTURE_STATUS.md`](docs/ARCHITECTURE_STATUS.md) for the status of
> older blueprints and proposals.

## Architecture

```text
Agent -- authenticated wiki_search/wiki_read --> Scout --> PostgreSQL 16 + pgvector/RLS
                                                          ^
raw/ + wiki/ --> sync-job (ingest identity) ----------------+

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
- Scout exposes exactly two retrieval tools, `wiki_search(query, department, k,
  seen)` and `wiki_read(path, department, mode)`, and never synthesizes an
  answer from retrieved text. Read modes are `tldr`, `outline`, one `section`,
  and `full`. `rag_fetch` is no longer an agent-facing MCP tool; the same
  engine call still backs `scripts/verify_addresses.py` and the `scout rag`
  CLI internally.
- PostgreSQL RLS applies the authenticated caller's canonical departments:
  `redteam`, `blueteam`, `ai_eng`, and `infra`.
- `rag_app_role` is the least-privilege query identity. `rag_ingest_role` is
  the least-privilege ingestion identity. Migration administration is confined
  to the one-shot migration/provisioning service.
- `host-sync` publishes immutable commit snapshots into the `vault-replica`
  volume and atomically retargets its `current` symlink. Scout and `sync-job`
  both mount that replica read-only; no developer working tree is mounted into
  either service, and no separate wiki-engine container reads it.

Wiki search and source retrieval share one embedding index: both are produced
through LiteLLM at 1024 dimensions (`scout/chunker.py`, `scout/ingest.py`),
distinguished only by a stored corpus tier, not a separate model or vector
space. There is no in-process FastEmbed and no 384-dimension wiki index.

The golden rule is simple: **search finds the page; the page is the answer,
cited by its path and heading.**

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

1. Call `wiki_search(query, department, k=5, seen=[])` against authenticated
   Scout to find candidate pages.
2. Call `wiki_read(path, department, mode="tldr")` on the selected page;
   escalate to `mode="outline"`, one `section`, or `full` only as needed.
3. Answer from the read page. A search snippet is never sufficient answer
   text.
4. Cite the wiki page's `path` and the heading used.

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

The compiler generates the page body **from the passages its minted address
retrieves**, not from the parsed file, so generation and the groundedness judge
read one corpus. It then judges the candidate against those same passages before
writing, retries once on an unsupported verdict carrying the rejected sentences,
and refuses to write a page it cannot ground. `--skip-groundedness` bypasses that
check and prints a warning; its output is unverified.

Two model calls are made per page — metadata (summary/entities/hint) before
minting, prose after — plus one judge call. Set `LITELLM_JUDGE_MODEL` to a model
other than `LITELLM_LLM_MODEL`: with both unset the judge is the same model that
wrote the prose, which self-preference bias makes a weak check.

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
export LITELLM_BASE_URL=http://127.0.0.1:54000/v1
# Export LITELLM_MASTER_KEY from your secret store; do not paste it into docs.
export SCOUT_INTEGRATION_URL=http://127.0.0.1:58080/mcp
export SCOUT_INTEGRATION_INFRA_TOKEN_FILE="$PWD/.secrets/scout_test_token"
uv run pytest -m integration --force-enable-socket -q
```

The integration override publishes every service on its own loopback port —
PostgreSQL `55432`, LiteLLM `54000`, Scout `58080`, host-sync `59000` — using
Compose's `!override` tag. That tag is load bearing: Compose merges port lists
additively, so without it the integration project also inherits the live
stack's `4000/8080/9000` and the two race for them. The failure is worse than
a collision — whichever project binds first
decides whether `pytest -m integration` exercises the disposable stack or the
live one. Earlier revisions of this section pointed the exports at `4000` and
`8080`, which were the live ports. Selected live tests fail with the names of missing prerequisites;
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
- [`docs/DEMO.md`](docs/DEMO.md): current end-to-end demonstration
- [`docs/ARCHITECTURE_STATUS.md`](docs/ARCHITECTURE_STATUS.md): active/historical document inventory
- [`docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md`](docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md):
  active proposal for handling sources that ingest cleanly but are not evidence;
  its findings are factual, its design is not implemented
- [`packages/snp-agent/`](packages/snp-agent): the portable distribution — an
  **Agent Plugins 1.0.0** plugin (`plugin.json` + `mcp.json` + `skills/`).
  `.agent/` is authoritative; `.claude/` and `packages/snp-agent/` are **tracked
  byte-for-byte mirrors** of the files they share with it, enforced by
  `tests/test_agent_package_sync.py` and `tests/test_docs_contract.py`. Edit
  `.agent/`, then mirror — a one-tree edit fails the suite.

  The trees are not identical, and the difference is a decision rather than an
  accident: the `superpowers-*` layer is **repo-local**. It is this repository's
  own development discipline, and shipping it would tell a consumer's agent to
  write brainstorms and plans into *their* `artifacts/superpowers/` for work
  unrelated to the memory system. `plugin.json` declares both what ships and
  what does not, and a test holds the package to that declaration in both
  directions. That layer is also absent from `.claude/`: Claude Code runs the
  `unlazy` gate discipline instead, and loading both put two conflicting
  completion protocols into one session. It remains in `.agent/`, which is what
  every other agent client reads, and the mirror test enforces the absence in
  both directions so it cannot widen into real drift. `plugin.json` / `mcp.json` live only in the package — `.agent/` is
  a working contract, not a plugin — while `package.json` is shared.

Documents explicitly marked historical or superseded preserve design context;
they are not deployment instructions.
