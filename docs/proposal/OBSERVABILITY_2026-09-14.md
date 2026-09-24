# Making the system explain itself to the agent

> **SUPERSEDED PROPOSAL.** The design is preserved; its O1–O5 ordering is not.
> That ordering rested on `stack_health` shipping without a migration, and the
> owner chose to wait for the tables on 2026-09-15. See
> `docs/AUDIT_2026-09-15.md`.

**Proposal, 2026-09-14. Nothing implemented.**

## Why this exists

During the demo the lead wanted to see a push become an index update. There was
no tool for it — a shell script (`scripts/demo/watch-sync.sh`) was written on the
spot to tail container logs. That script is a workaround for a missing
capability: **the system cannot tell an agent what happened inside it.**

The owner's framing, which is the right one:

> *"Why don't we have tools like that for when in future, it reported back into
> agent then tell user what had happened inside — something human can't see with
> naked eyes."*

---

## 1 · Two thirds of this is already built

### The events exist

`sync-job` emits structured JSON for every stage, keyed by commit:

```json
{"stage": "row_visible", "correlation_id": "447d9198…", "source_uri": "Banana.md",
 "doc_id": "2100e7e7…", "chunk_count": 2, "elapsed_ms": 1720.749,
 "observed_at": "2026-09-14T08:02:07.928+00:00"}
```

Seven stages, observed live: `watcher_wake`, `chunk_complete`,
`embed_request_sent`, `embed_response_received`, `postgres_commit`,
`row_visible`, `cycle_complete`. Cycle events carry
`indexed_count` / `unchanged_count` / `deleted_count`.

**`correlation_id` is the commit SHA.** That is exactly the join key a
"what happened to my push" question needs.

### The storage pattern exists

`config/postgres/migrations/005_ingest_events.sql` defines an append-only audit
table: immutable to the identity it audits, `GRANT INSERT, SELECT` with
`UPDATE/DELETE/TRUNCATE` revoked, indexed on `(source_uri, occurred_at DESC)`.
Its migration comment records two rejected designs before this one.

**It holds 0 rows.** Its `CHECK` permits a single `event_type`
(`capability_override`), so it was scoped to one narrow audit case and never
became the ingest log.

### What is missing — three links

```
  sync-job ──JSON per stage──▶ container logs ──▶ discarded
                                     │
                nothing persists ────┘
                                     ▼
                          (no events table)
                                     │
                  nothing reads ─────┘
                                     ▼
                              (no MCP tool)
```

Nobody has to invent the event vocabulary or the storage pattern. Both exist.

---

## 2 · Two tools

Decided: two, not one and not three. *"Tell me about this thing"* and
*"tell me about everything"* are different questions, and a single polymorphic
tool reads badly to a model.

### `wiki_trace(page | commit)` — levels 1 and 2

| Asked | Answer |
|---|---|
| `wiki_trace(page="concepts/MCP.md")` | is it indexed, when, how many chunks, which commit put it there |
| `wiki_trace(commit="447d919")` | the full stage timeline for that push: what changed, what stayed, what failed, how long |

Level 1 is answerable **today** from `rag_documents` alone. Level 2 needs the
event table.

### `stack_health()` — level 3

| Field | Source | Available today |
|---|---|---|
| snapshot commit | replica `current` symlink | ✅ |
| documents indexed | `rag_documents` | ✅ |
| last ingest | `max(ingested_at)` | ✅ |
| embedding coverage | `count(embedding is not null)` | ✅ |
| recent failures | event table | ❌ needs §3 |
| CI verdict | `health_reports` | ❌ needs §4 |

**Four of six fields work with no new storage.** That is why this ships first.

`stack_health()` deliberately does **not** report container health, image
revision or DNS — those need the docker socket, which a server cannot and should
not have. Those stay operator work.

---

## 3 · Table one: `sync_events`

A new table, not an extension of `ingest_events`. That table is a scoped
*ingest* audit whose `CHECK` allows one event type; health and pipeline stages
are not ingest events, and mixing them muddies a deliberately narrow design.

Mirror the JSON already emitted — no new vocabulary:

```
  event_id        bigint identity
  occurred_at     timestamptz        ← observed_at
  correlation_id  text               ← the commit SHA; the join key
  stage           text               ← the seven stage names, CHECK-constrained
  source_uri      text NULL          ← null on cycle events
  doc_id          uuid NULL
  elapsed_ms      double precision
  detail          jsonb              ← counts and anything stage-specific

  INDEX (correlation_id, occurred_at)
  INDEX (source_uri, occurred_at DESC)
```

Same privilege shape as `ingest_events`: `INSERT, SELECT` to the writer,
`UPDATE/DELETE/TRUNCATE` revoked. Append-only as a property of the database,
not of today's code.

**Retention needs a decision** — seven stages per document across 433 documents
is ~3k rows per full re-index. Unbounded growth is a slow leak. Suggest a
`DELETE` older than N days in the same job, with N as an owner call.

---

## 4 · Table two: `health_reports`, carrying SARIF

CI's verdict, in the standard format for findings.

**SARIF** was chosen over a bespoke shape because it is the settled 2026
standard for lint and scan results — file, line, rule, severity — and GitLab
consumes it natively, so the Phase-1 migration needs no rework. OpenTelemetry
CI/CD semantic conventions were considered and **rejected for now**: they are at
Release Candidate, they answer *"how long did the pipeline take"* rather than
*"is the vault healthy"*, and they would require a collector and backend for a
single-box system that currently has **zero CI runners registered**.

```
  report_id     bigint identity
  occurred_at   timestamptz
  source        text          ← 'vault-lint' | 'secret-scan' | …
  revision      text          ← commit the check ran against
  ok            boolean
  sarif         jsonb         ← the SARIF document
```

`stack_health()` returns the newest row per `source`, plus its age — so an agent
can say *"the vault lint last ran 3 days ago and found 4 problems"*, or
*"no lint has ever run"*, which is the honest answer today.

---

## 5 · What CI has to become first

*"CI reports via that tool"* needs four links, and three do not exist:

| Link | State |
|---|---|
| a registered runner | ❌ `actions/runners` returns `total_count: 0` |
| a vault-health workflow | ❌ `checks.yaml` checks **code** (ruff/mypy/pytest), not the vault |
| `security.yaml` | ❌ broken — defect #26, has never scanned a commit |
| a place to write results | ❌ this proposal |

This is a build, not a wiring job. It is also why `stack_health()` must not wait
for it.

---

## 6 · Order

| Phase | What | Depends on |
|---|---|---|
| **O1** | `stack_health()` with the four DB/replica fields | nothing |
| **O2** | `wiki_trace(page=…)` — level 1 | nothing |
| **O3** | `sync_events` table + persist the emitted stages | O1 |
| **O4** | `wiki_trace(commit=…)` — level 2; failures in `stack_health()` | O3 |
| **O5** | `health_reports` + SARIF emitter in a vault-lint workflow | a runner existing |

**O1 and O2 ship with no migration and no new writes.** They read what is
already in PostgreSQL and the replica. That alone replaces `watch-sync.sh` with
something an agent can answer from.

---

## 7 · Open

- **Tool naming.** Server is `snpwiki`; the owner prefers bare tool names since
  the client prefixes by server (`snpwiki_search`). Under that scheme these
  become `snpwiki_trace` and `snpwiki_health`. **Unresolved risk:** a client
  that does not namespace by server would show bare `trace` / `health` / `read`,
  colliding with the agent's own tools. Not yet decided.
- **Retention** for `sync_events`.
- **Does `wiki_trace` belong to the retrieval server at all?** Scout is the
  retrieval door. Bolting "why did my push fail" onto it may repeat the category
  error that produced two servers with identical tool names. The alternative is
  a separate observability surface — which reintroduces a second server. Stated
  here rather than silently decided.
