# Demo — Authenticated Wiki Retrieval

This demonstration shows the system's intended behavior: `wiki_search` finds
candidate pages, `wiki_read` returns the canonical page, and the read mode
escalates — `tldr` → `outline` → `section` → `full` — only as far as the
answer needs. A search snippet is never answer text.

## Prerequisites

1. Run `./scripts/bootstrap.sh`, configure Cloud API and Scout authentication,
   then start the stack with `docker compose up -d --build`.
2. Confirm `docker compose ps` and `http://127.0.0.1:9000/ready` are healthy.
3. Connect an MCP client to authenticated Scout. JWT/static Scout clients
   must send `Authorization: Bearer <token>`; the token must include the page's
   department. See [`CONNECT_AGENTS.md`](CONNECT_AGENTS.md).
4. Confirm the sample sources have been indexed by `sync-job`.

## Demonstration flow

Ask a question covered by an existing wiki page, for example a high-throughput
inference question related to the checked-in vLLM sources.

| Step | Action | Expected behavior |
|---|---|---|
| Search | `wiki_search(query, department, k=5, seen=[])` | Scout returns an envelope: `results` (bounded snippets, plus `seen`/`degraded` flags), `returned`, `suppressed_as_seen`, and `has_more`. |
| Read | `wiki_read(path, department, mode="tldr")` | Scout returns the canonical page envelope: `path`, `title`, `type`, `tldr`, `content_hash`. |
| Decide | Evaluate whether the page is sufficient. | If it is sufficient, stop and cite the page and heading; a search snippet is never answer text. |
| Escalate | Re-call `wiki_read` with `mode="outline"`, one `section`, or `full` only as needed. | Pull only the granularity the answer actually requires. |
| Answer | Cite the read page's `path` and the heading used. | Source extraction beyond the indexed page is not yet an agent tool; say so plainly rather than fabricating a quotation. |

Use the `path` exactly as `wiki_search` returned it. Do not substitute a
plausible filename or hand-written path for a demo.

## Show the fail-closed boundary

- Repeat the Scout call without a bearer token in JWT/static mode: it must be
  rejected.
- Repeat it with a token lacking the page department: `wiki_search` must not
  surface the page and `wiki_read` must not return it.
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
| Wiki search is empty | host-sync `/ready`, `/vault-replica/current/wiki`, then Scout startup logs |
| Scout returns 401/403 | bearer token validity, issuer/audience, and canonical department claim |
| A read page lacks the needed evidence | source extraction beyond the indexed page is not yet an agent tool; report the limit rather than fabricating a source |
| Verifier exits `2` | PostgreSQL/model/network/auth configuration; do not heal |
| Verifier exits `1` | semantic address health; use the closed-loop gate on a feature branch |
