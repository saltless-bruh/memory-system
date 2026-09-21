# ADR-0005 — Repair the corpus by bumping PARSER_REVISION

- **Status:** Accepted · 2026-09-15, not yet executed
- **Deciders:** repository owner

## Context

`raw/papers/computers-12-00091.pdf` was ingested with `figure_count=7` and
`figures_described=0`. Root cause measured: a daemon restart started `sync-job`
and `litellm` in the same second — `depends_on: service_healthy` is honoured on
`compose up`, not on a daemon restart — and every vision call returned
`[Errno 111] Connection refused` while the gateway booted. The vision path has no
retry, unlike the embed path.

It cannot self-heal. `_IndexedSignature.matches()` compares content hash, model,
chunk policy and `capability_fingerprint`; the fingerprint is byte-identical
before and after, so the document reports `unchanged` forever.

## Decision

Bump `PARSER_REVISION` 2 → 3, **after** the retry and the new extraction state
land — not as a repair route in its own right.

`capabilities.py` states the rule: *"Bump on any change to what the parser
produces from the same bytes."* Adding a retry and a distinct zero-described
state both change that. So the bump is required by the module's own contract, and
restoring the seven figure descriptions is a side effect.

Rejected: deleting the one document's rows. Cheaper (110 chunks versus a full
re-parse) but leaves `PARSER_REVISION` at 2 while the parser has changed — the
exact drift the field exists to prevent.

## Consequences

- **Worse:** re-parses and re-embeds **436 documents / 2099 chunks** through the
  cloud route, at cost and time.
- **Better:** the revision stays honest, and the corpus is repaired without a
  second mechanism.
- **Ordering:** must run after the retry fix, or the re-parse reproduces the loss.
