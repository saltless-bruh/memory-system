---
name: snp-bootstrap-system
description: "Use this skill to bootstrap, start, restart, or inspect the SNP Memory System V3 infrastructure with Docker, PostgreSQL, LiteLLM, Scout, and ingestion services."
---

# Bootstrap the SNP Memory System

Use the repository scripts and Compose dependency graph; do not start runtime
services against a pending schema.

1. Run `./scripts/bootstrap.sh`.
2. Populate `.env` with cloud-provider credentials and Scout authentication.
3. Keep migration, query, and ingestion database identities separate.
4. Run `docker compose up -d --build`.
5. Confirm `postgres-migrate` completed before Scout and sync-job, then inspect
   `docker compose ps` and the documented readiness endpoints.

Scout is the sole MCP server. Clients list it as `snpmemory` and reach it over
authenticated Streamable HTTP; it serves `wiki_search`, `wiki_read`, and
`wiki_quote`. Authoring and verification are `snpmemory` CLI commands run from
a checkout, not MCP tools. Development authentication is loopback-only; remote
clients use a bearer token.

Do not infer health from a running container alone. Report a failed migration,
credential error, or unavailable embedding provider as infrastructure failure.
