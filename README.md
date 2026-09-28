# SNP Memory System

SNP is a self-hosted memory system for coding agents and engineering teams.
Git-backed Markdown pages provide a compact, compiled knowledge map; the same
PostgreSQL 16 with pgvector index also stores source chunks used by the
operator's compilation pipeline. Agents retrieve wiki pages through
authenticated Scout: `wiki_search` finds candidates, `wiki_read` returns a
canonical page, and `wiki_quote` returns the verbatim `raw/` passage behind one
of that page's `sources[]` entries.

> Before operating on the vault, read [`AGENTS.md`](AGENTS.md). It is the
> authoritative query, page, citation, and PR-first contract. See
> [`docs/ARCHITECTURE_STATUS.md`](docs/ARCHITECTURE_STATUS.md) for the status of
> older blueprints and proposals.

## Architecture

```text
Agent -- authenticated wiki_search/wiki_read --> Scout --> PostgreSQL 16 + pgvector/RLS
                                                  |                 ^
                                                  v                 |
                                           canonical replica        |
raw/ + published wiki/ --> sync-job (ingest identity) ----------------+

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
- Scout exposes exactly three retrieval tools, `wiki_search(query, department,
  k, seen)`, `wiki_read(path, department, mode)`, and `wiki_quote(path, hint,
  department)`, and never synthesizes an answer from retrieved text. Read
  modes are `tldr`, `outline`, one `section`, and `full`. `wiki_quote` resolves
  a `sources[]` entry to passages post-filtered to that one file, or returns
  `status: "no_source"`. `rag_fetch` is no longer an agent-facing MCP tool;
  `wiki_quote` and `scripts/verify_addresses.py` call the same engine function
  internally.
- PostgreSQL RLS applies the authenticated caller's canonical departments
  (`redteam`, `blueteam`, `ai_eng`, `infra`) to every indexed row. Source
  chunks carry the departments `raw/.acl.yaml` grants them, so source
  retrieval is department-scoped. Wiki pages are **not yet department-scoped**:
  ingestion grants every page all four departments, whatever its frontmatter
  says, and `wiki_read` reads a page by path without comparing departments.
  Per-department wiki restriction is planned, not present.
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
source .venv/bin/activate
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

For the OpenCode rehearsal, run the explicit live preflight from this
checkout with the intended credentials:

```bash
docker compose exec -T sync-job test -f /tmp/snp-sync-job/ready
.venv/bin/python scripts/rehearsal_preflight.py --live --output json
```

The marker is inside `sync-job`, not the host's `/tmp`. The preflight checks
required services and migration completion, host-sync readiness, a real
1024-dimensional embedding, generation, an explicit typed judge response, and
authenticated Scout search then canonical read. It spends provider requests
on synthetic probe inputs. Liveness checks alone cannot establish readiness.
Without `--live`, it makes no network/Docker calls and reports unverified.
Exit `0` means all checks passed, `1` a live check failed, and `2`
configuration or unverified status. A passing preflight does not certify
fresh-agent answers, ingestion, PR review, or edit propagation.

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

For an existing fresh OpenCode project directory, preview and install from
the system checkout:

```bash
snpmemory mcp-config --client opencode
snpmemory install-agent /path/to/project --client opencode --dry-run
snpmemory install-agent /path/to/project --client opencode
```

The native config uses `mcp.snpmemory` with `type: "remote"`, the loopback
URL, and `{env:SCOUT_AUTH_HEADER}`. That entry is the remote retrieval server:
it is reached over the network and launches nothing locally. (The name once
belonged to a local stdio server, deleted in leaf-4.3; the remote server took
it in leaf-4.4, and an older config naming that server `scout` has the old
entry removed rather than kept alongside.)
Installation copies seven skills to `.opencode/skills/` and governing files
to `.opencode/snp/`; config `instructions` loads rules and instructions.
Workflow files do not automatically register slash commands or a plugin.
The default portable installation and other client exporters retain their
existing behavior. See the [repeatable OpenCode rehearsal](docs/DEMO_OPENCODE.md)
for fresh-profile restrictions, discovery checks, real OpenShift content,
human PR handoff, and the ten-edit latency experiment.

## Query and authoring workflow

1. Call `wiki_search(query, department, k=5, seen=[])` against authenticated
   Scout to find candidate pages.
2. Call `wiki_read(path, department, mode="tldr")` on the selected page;
   escalate to `mode="outline"`, one `section`, or `full` only as needed.
3. Answer from the read page. A search snippet is never sufficient answer
   text.
4. Cite the wiki page's `path` and the heading used.
5. When the answer needs the passage under a claim, call
   `wiki_quote(path, hint, department)` with a `path`/`hint` pair from the read
   page's `sources[]`. Report `status: "no_source"` as it is; never write a
   quotation the tool did not return.

The operator handles ingestion and compilation in an authorized checkout;
the fresh retrieval agent does not gain filesystem or database authority.
Preserve original `raw/` evidence, apply the intended source ACL, and use a
dedicated source directory to bound the ingestion scan. Review a plan with
actual source locators before spending generation/judge calls.

For the rehearsal, `snpmemory compile-plan PLAN --dry-run` prepares grounded
prose in foreground staging. This compiler dry run can spend model calls and
write a sibling plan staging directory; it withholds wiki publication. Host
CLI calls use `LITELLM_BASE_URL=http://127.0.0.1:4000/v1`, and compilation
uses the gateway alias `LITELLM_LLM_MODEL=snp-llm`. Set these only for the
CLI child process, as shown in the rehearsal; Compose uses the latter name
for its upstream provider model. Do not combine compiler `--dry-run` with
`--background`, whose current CLI path does not forward `dry_run`. The
legacy publisher regenerates `wiki/index.md`, so use the rehearsal's staged
candidate handoff instead of publishing through that path.

Prepare the final page on a feature branch against the target vault's
authoritative `SCHEMA.md` before human handoff, preserving authored
`index.md`, `log.md`, and unrelated work. The legacy compiler omits the H1
and required V3 metadata, so staged prose needs an explicit authoring pass.
Check required frontmatter, H1, provenance when sources are declared, and
`## Cross-References` with at least two real outbound `[[wikilinks]]`.
Keep sources as provenance and links in the body. The
automated vault checker is transitional and does not certify the complete
V3 schema/heading contract; grounding and explicit page review are separate
requirements. `--skip-groundedness` cannot satisfy the rehearsal gate.

The human publishes the reviewed feature branch, opens/reviews the PR, and
merges it. No exposed agent tool pushes or merges; never push agent changes
directly to `main` or `master`. Do not commit wiki edits without the requested
repository action. After the human merge, verify the changed answer through
Scout search then canonical read in the same authorized scope.

## Verification

The deterministic suite is offline and prohibits network sockets:

```bash
timeout 300s uv run pytest -m 'not integration' --disable-socket -q
uv run ruff check .
uv run mypy scout scripts
```

These code checks are not vault retrieval or full V3 page certification.
Agents use Scout for content verification; the operator/human separately
reviews the final candidate against its target schema.

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

> **Run those exports in a dedicated shell.** The offline test fixture in
> `tests/conftest.py` now clears `LITELLM_BASE_URL` and `LITELLM_MASTER_KEY`
> for non-integration tests, preventing the earlier ambient-gateway failures.
> Keep integration exports out of other operator commands by closing that
> shell when the live checks finish.

Legacy address verification is an operator check requiring live services;
it is not an agent retrieval path or a complete V3 page check:

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
`FAIL`/`DRIFT`, and `2` means infrastructure or configuration failure. Exit `2`
is never a content finding and never a reason to edit a page.

Nothing runs this check automatically, and nothing repairs a drifted address.
The closed-loop gate (`scripts/ci_address_gate.py`), its heal step and the
`auto-healer.yaml` workflow were removed on 2026-09-06 (29f1f50). CI in
`.gitea/workflows/` is `checks.yaml` (the offline suite, lint, format and type
check) and `security.yaml` (the secret scans); neither runs a vault, address
or groundedness verification. On exit `1`, re-mint the address or revise the
page on a feature branch and hand it to human PR review.

## Documentation

- [`AGENTS.md`](AGENTS.md): authoritative agent operating contract
- [`docs/runbook.md`](docs/runbook.md): deployment and incident operations
- [`docs/DEMO.md`](docs/DEMO.md): current end-to-end demonstration
- [`docs/DEMO_OPENCODE.md`](docs/DEMO_OPENCODE.md): repeatable fresh OpenCode
  setup, real OpenShift source, human-reviewed PR, and measured edit propagation
- [`docs/ARCHITECTURE_STATUS.md`](docs/ARCHITECTURE_STATUS.md): active/historical document inventory
- [`docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md`](docs/SOURCE_HEALTH_AUDIT_AND_PROPOSAL.md):
  active proposal for handling sources that ingest cleanly but are not evidence;
  its findings are factual, its design is not implemented
- [`packages/snp-agent/`](packages/snp-agent): the portable distribution — an
  **Agent Plugins 1.0.0** plugin (`plugin.json` + `mcp.json` + `skills/`).
  **It is the source of truth for every file it ships.** `.agent/` and
  `.claude/` are **tracked byte-for-byte mirrors** generated from it, enforced
  by `tests/test_agent_package_sync.py` and `tests/test_docs_contract.py`. Edit
  the package, then run `python3 scripts/export_agent_bundle.py --sync`, which
  copies it into `.agent/` and the four contract subtrees into `.claude/`; an
  edit made only in a mirror is overwritten by the next sync, and a one-tree
  edit fails the suite. (`--direction agent-to-packages` copies the declared
  files the other way; it is a recovery path for an edit already made in
  `.agent/`, not the workflow.)

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
  both directions so it cannot widen into real drift. That repo-local layer,
  and the development instructions `plugin.json` lists under `repoLocal`, are
  the only files edited in `.agent/` directly, because the package does not
  ship them. `plugin.json`, `mcp.json` and `package.json` are synced into
  `.agent/` byte-for-byte but not into `.claude/`, since they describe the
  distribution rather than a client's contract.

  Claude Code loads `CLAUDE.md`, `.claude/rules/` and `.claude/skills/` on its
  own. The mirrored `.claude/instructions/` and `.claude/workflows/` are kept
  for byte parity with every other client; Claude Code does not load them by
  itself, so a rule that matters to a Claude session belongs in `rules/`.

Documents explicitly marked historical or superseded preserve design context;
they are not deployment instructions.
