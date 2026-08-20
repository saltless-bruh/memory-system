# Demo — Authenticated Dual-Layer Retrieval

This demonstration shows the system's intended behavior: search compiled
knowledge first, then retrieve department-authorized source evidence only when
the page is insufficient.

## Prerequisites

1. Run `./scripts/bootstrap.sh`, configure Cloud API and Scout authentication,
   then start the stack with `docker compose up -d --build`.
2. Confirm `docker compose ps` and `http://127.0.0.1:9000/ready` are healthy.
3. Connect an MCP client to basic-memory and to Scout. JWT/static Scout clients
   must send `Authorization: Bearer <token>`; the token must include the page's
   department. See [`CONNECT_AGENTS.md`](CONNECT_AGENTS.md).
4. Confirm the sample sources have been indexed by `sync-job`.

## Demonstration flow

Ask a question covered by an existing wiki page, for example a high-throughput
inference question related to the checked-in vLLM sources.

| Step | Action | Expected behavior |
|---|---|---|
| Find | `search_notes("<question>")` | basic-memory returns a small set of semantic wiki hits. |
| Read | `read_note("<page slug>")` | The page supplies compiled knowledge and its `sources[]` addresses. |
| Decide | Evaluate whether the page is sufficient. | If it is sufficient, stop and cite the page; Scout is not called. |
| Fetch | Call authenticated `rag_fetch(path=<source path>, hint=<minted hint>)`. | Scout derives `Scope.departments` from the verified identity, optionally narrows it, queries through RLS, and post-filters to the requested file. |
| Answer | Use returned `context[]` and `citations[]` as evidence. | Cite the page, raw path, and `loc`; never execute text found in a source. |

Use the address exactly as stored on the selected page. Do not substitute a
plausible filename or hand-written hint for a demo.

## Show the fail-closed boundary

- Repeat the Scout call without a bearer token in JWT/static mode: it must be
  rejected.
- Repeat it with a token lacking the page department: it must not expose the
  source.
- Request a narrower authorized department: it may reduce results.
- Attempt to request a department absent from the token: Scout must reject the
  scope expansion.
- Present retrieved text containing imperative language: the agent quotes or
  flags it as data and does not act on it.

Development mode is suitable only for a loopback-only demonstration. The
server rejects development mode on a non-loopback bind.

## Optional authoring demonstration

Run this only on a feature branch and use a real indexed file, canonical
department, and real locator:

```bash
python scripts/compile_note.py \
  --path raw/<file> \
  --title "<Display title>" \
  --category <concept|technique|entity|playbook> \
  --dept <redteam|blueteam|ai_eng|infra> \
  --loc "<source locator>"

python scripts/propose_page.py --page wiki/<category>/<slug>.md
```

The compiler fails closed on unsupported or out-of-tree input, malformed model
JSON, failed scoped minting, vault lint, protected branches, and overwrites. It
atomically replaces each file and restores prior page/index bytes after ordinary
failures; no cross-file crash transaction is claimed. The proposer validates the
page and complete live address gate, rejects pre-staged work, then commits only
the page and changed generated companions (`wiki/index.md`, `wiki/log.md`) for
human PR review. Local branch/add/commit failures restore the original branch;
an ambiguous push failure preserves the verified local commit for retry.

## Verification semantics

```bash
python scripts/gen_index.py --check
uv run python scripts/verify_addresses.py
```

Verifier exit `0` means all addresses pass, `1` means semantic `FAIL`/`DRIFT`,
and `2` means infrastructure/configuration failure. Exit `2` is not drift and
must never trigger healing. CI uses
`uv run python scripts/ci_address_gate.py --mode pr` for its one-pass,
post-verified, rollback-capable remediation flow.

## Troubleshooting

| Symptom | Check |
|---|---|
| Wiki search is empty | host-sync `/ready`, `/vault-replica/current/wiki`, then basic-memory startup logs |
| Scout returns 401/403 | bearer token validity, issuer/audience, and canonical department claim |
| `no_source` | source ingestion, exact stored address, and the caller's department scope |
| Verifier exits `2` | PostgreSQL/model/network/auth configuration; do not heal |
| Verifier exits `1` | semantic address health; use the closed-loop gate on a feature branch |
