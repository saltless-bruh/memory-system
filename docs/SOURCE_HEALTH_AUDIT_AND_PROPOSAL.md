# Source Health — Audit and Design Proposal

> **Status: ACTIVE PROPOSAL (not implemented).** Nothing described under
> "Proposed design" exists in the codebase today. The "Findings" section below
> records verified defects in the current system and is factual. Dated
> 2026-08-19, against branch `fix/architecture-security-hardening` at `92f5b42`
> plus the in-flight remediation of `artifacts/superpowers/plan.md`.

## What this document covers

This is a **second, narrower audit** than
[`artifacts/superpowers/audit-2026-08-19-v2-system.md`](../artifacts/superpowers/audit-2026-08-19-v2-system.md).
That one asked *"is the system real and does it work?"*. This one asks a
question that only surfaced afterwards:

> **What happens when a source file is intact enough to ingest, but not good
> enough to be evidence?**

The answer today is: nothing good, and nothing visible.

### Relationship to the main audit

Two items here were first recorded in that audit's `FUTURE WORK` section and are
expanded with evidence and a concrete design: the vault linter's blindness
(SH-2) and the missing quarantine signal (SH-4). The remaining eight findings
are new. Findings from the main audit (B1–B3, M1–M8, m1–m11, NEW-1) are **not**
repeated here.

---

## 1. The framing problem: these files are not corrupted

Two files in `raw/` motivated this work. Neither is corrupt.

### `raw/images/inference_dashboard.png`

```
$ file raw/images/inference_dashboard.png
PNG image data, 64 x 64, 8-bit/color RGBA, non-interlaced      # 155 bytes
```

A structurally valid PNG. Every integrity check passes: magic bytes correct,
CRC valid, decodes without error. It is simply a 64×64 placeholder with no
dashboard in it. The vision model is the only component that notices:

```
400 INVALID_ARGUMENT — "Unable to process input image"
```

### `raw/reports/vllm_high_throughput_serving.pdf`

1.5 KB, 3 pages. `pypdf` parses it cleanly and extracts text from every page:

| Locator | Extracted characters | Content |
|---|---|---|
| p.1 | 103 | *"Page 1: Executive Summary - High-Throughput vLLM Serving…"* |
| p.2 | 131 | *"Page 2: Technical Specifications - PagedAttention KV-Cache…"* |
| p.3 | 106 | *"Page 3: Performance Evaluation - Latency Benchmarks…"* |

One sentence per page. No parser error, no warning, no failure of any kind.
This file is cited as a *"vLLM Serving Technical Report"* by **four** wiki pages
and is the "haystack" in the `finish.md` Needle-in-a-Haystack scorecard.

### Why this matters for the feature

A corruption detector — checksums, magic bytes, "does it parse" — **passes both
files clean**. Whatever gets built must judge *fitness for purpose*, and it must
do so by measuring **parser output**, not file structure.

**Naming recommendation:** do not call this feature "corruption detection". Both
motivating files are valid. That name will lead an implementer toward checksum
validation and straight past the actual failure mode. Call it *source health* or
*evidence fitness*.

---

## 2. Failure taxonomy

"Unusable source" is at least five distinct conditions with radically different
detection costs. This distinction is what makes a 1 GB corpus tractable or not.

| Class | Example | Detection | Cost |
|---|---|---|---|
| **A. Structurally invalid** | truncated PDF, bad docx zip container | parse attempt raises | free |
| **B. Valid but empty** | 155-byte PNG, 0-row CSV, scanned PDF with no text layer | parser yields 0 sections | free |
| **C. Valid but insufficient** | the 1.5 KB "technical report" | yield below a threshold | ~free (a ratio) |
| **D. Valid but model-rejected** | Gemini `INVALID_ARGUMENT` on the PNG | **a model call** | **paid, rate-limited** |
| **E. Changed since ingest** | bit rot, partial write, bad sync, overwrite | stored hash comparison | free |

Only class D costs money. Every design decision below follows from keeping D as
rare as possible.

Note that class B is currently **indistinguishable from class D** in the stored
data, because both produce zero chunks and the reason is discarded (see SH-4).

---

## 3. Findings

### SH-1 — Integrity checks cannot detect the failures that actually occur
**Severity: framing (drives everything else)**

Evidence: §1 above. Both motivating files pass every structural check.

**Solution.** Judge parser output, not file bytes. Concretely, after
`scout.parsers.parse_file` returns, evaluate:
- section count (`0` → class B)
- total extracted characters
- **characters per unit** — per PDF page, per image, per CSV row, per KB of
  source. This ratio is what separates a real 40-page report from a 3-sentence
  stub, and it is scale-free.

Thresholds must be per-type: 100 characters is fine for a JSON config and absurd
for a technical report.

---

### SH-2 — The vault linter accepts a source that yields no evidence
**Severity: high · cheapest fix in this document**

`scripts/gen_index.py` validates that each `sources[].path` **exists on disk**
(rule R-1.4). It does not, and cannot, know whether that path produced any
indexed chunk. Consequences observed live:

- `wiki/concepts/model-routing-gateway.md` cites `raw/images/inference_dashboard.png`,
  a document with **zero chunks**, and the vault lints clean at
  `13 pages · 0 errors · 0 warnings`.
- Four wiki pages cite the 3-sentence PDF as a technical report. Clean lint.

A zero-byte file placed at a cited path would also pass.

**Solution.** Extend the linter so each `sources[].path` must resolve to **at
least one indexed chunk**, not merely exist. This requires a database read, so
it belongs beside `verify_addresses.py` (which is already live-service-gated)
rather than in the offline lint, or in the offline lint behind an opt-in flag.

*This single check would have caught both motivating files before either reached
a wiki page.* Estimated cost: ~30 lines.

---

### SH-3 — Provenance can diverge silently from the evidence served
**Severity: high · least obvious · worse than an outage**

Scout never reads `raw/`. It reads chunks from PostgreSQL — this is deliberate
(R-4.2, R-5.4) and load-bearing. Therefore when a file on disk is damaged:

1. The stored chunks remain intact.
2. Retrieval continues to succeed and returns **correct** text.
3. Nothing fails, nothing warns.

But the citation now points at a file that no longer contains the quoted text.
The agent cites `raw/x.pdf p.2` with full confidence, and p.2 no longer says
that. **The failure is invisible and it looks like success** — which is strictly
worse than an error, and is the same class of problem as the fabricated image
descriptions this remediation removed.

**Non-solution to avoid.** Do *not* make Scout stat the filesystem at fetch
time. `raw/` is not mounted into the Scout container by design; mounting it
would reverse a deliberate isolation boundary for a check that belongs
elsewhere.

**Solution.** Record `content_sha256` on `rag_documents` at ingest. `sync-job`
already watches `raw/` with `watchfiles` and fires within seconds of any
modification — the detection mechanism exists. Add the comparison so a change is
classified rather than silently re-ingested:

| Hash | Re-parse result | Meaning | Action |
|---|---|---|---|
| unchanged | — | no-op | skip (this is also the cost gate, SH-5) |
| changed | healthy | legitimate edit | re-ingest normally |
| changed | unhealthy | **damage** | quarantine + alert, do not discard silently |

---

### SH-4 — A quarantined source is indistinguishable from "no match"
**Severity: high**

`FetchStatus` has exactly two values: `OK` and `NO_SOURCE`. An agent that asks
for evidence from an unusable source receives `no_source` — the same response it
gets when a hint simply does not match. It will reasonably report "no relevant
source found" when the truth is "this source is known to be broken".

The same conflation exists one layer up. `scripts/verify_addresses.py`
classifies `PASS` / `DRIFT` / `FAIL`, where DRIFT means *retrieved a different
file* and FAIL means *retrieved nothing*. An unusable source is neither: the
address is not stale and the hint is not wrong — **the evidence does not
exist**. It currently reports as DRIFT, which misdirects an operator to
`/snp-heal`, which cannot possibly help because there are no chunks to re-mint
against. This was observed live:

```
DRIFT wiki/concepts/model-routing-gateway.md#1 -> raw/images/inference_dashboard.png
```

**Solution.** Add a distinct status at both layers:
- `FetchStatus.SOURCE_QUARANTINED` beside `OK` / `NO_SOURCE`, so the agent is
  told the source is untrustworthy. This stays consistent with the injection
  guard (R-8.5): it is still data, still carries no action field, and still
  instructs the agent to do nothing.
- `VerifyStatus.NO_EVIDENCE` beside `PASS` / `DRIFT` / `FAIL`, routing an
  operator to *fix the source* rather than *fix the address*.

Also required: failure state must **survive**. Today `parse_image` records
`metadata["vlm_status"]` and `metadata["vlm_error"]` on chunks — but a document
with zero chunks is deleted (`purged_empty`), so the diagnosis is destroyed with
it. The reason a source failed must be persisted independently of whether it
produced chunks.

---

### SH-5 — A daily full rescan re-runs paid model calls on unchanged files
**Severity: medium (cost and availability)**

Class D detection requires a model call per file. During the main audit, a
**ten-file** corpus produced Gemini `429 — No deployments available` after a
handful of vision calls. Extrapolating an unconditional daily scan to a 1 GB
mixed corpus is a budget and rate-limit problem, not a compute problem.

Only two things change between scheduled runs: the file changed, or the
validator changed (new parser version, new model, new thresholds).

**Solution.** Hash-gate the expensive work.

- Store `content_sha256` **and** `validator_version` per document.
- A daily run hashes everything — reading 1 GB is seconds on SSD and costs
  nothing.
- Re-validate only where the hash differs **or** `validator_version` is behind.
- Bump `validator_version` deliberately when parser or thresholds change, which
  triggers exactly one controlled re-sweep.

This turns the daily cost from `O(files × model calls)` into `O(bytes read)`.

---

### SH-6 — A separate validator will drift from the ingest parser
**Severity: medium (architectural)**

A health checker with its own parsing logic eventually disagrees with
ingestion: "the validator says this file is healthy, but ingestion produces
nothing from it." That is the same class of divergence — two sources of truth
for one fact — that the main audit found throughout this system.

**Solution.** **One parser, two callers.** The health check must call
`scout.parsers.parse_file` and judge *its* output. It must never re-implement
parsing.

Note this makes the `fresh` mode far smaller than it appears:
`scout/ingest.py` already exposes `--dry-run`, documented as *"Parse and chunk
without writing to PostgreSQL"*, and already reports per-file chunk counts. A
health check is largely that call plus thresholds plus a report.

---

### SH-7 — Parsing untrusted uploads at scale is a new attack surface
**Severity: high (security) · most likely to be underestimated**

This system's stated premise is that `raw/` may contain hostile content
(R-8.5). A bulk health checker proposes to parse 1 GB of arbitrary uploaded
files, which turns a data-quality feature into an execution surface:

- **Decompression bombs.** `.docx` and `.xlsx` are ZIP containers. So is any
  archive a user uploads.
- **XML external entity / billion-laughs expansion.** Affects `.docx`, `.xlsx`,
  **and `.svg`** — all of which are XML.
- **PDF parser pathologies** — malformed cross-reference loops, deeply nested
  object graphs.
- **Existing exposure worth noting:** `scout/parsers.py:extract_image_via_vlm`
  base64-encodes SVG and sends it to a vision model. SVG is XML and can carry
  scripts and external references. This path exists today.

**Solution.** Treat the checker as a hostile-input processor from day one:

| Control | Requirement |
|---|---|
| Per-file timeout | hard wall-clock kill, not a soft deadline |
| Decompression ratio cap | reject beyond a fixed expansion factor |
| XML parsing | entity resolution and external DTDs disabled |
| Memory | bounded per file; stream, never load 1 GB into RAM |
| Concurrency | capped worker pool — also protects the model rate limit |
| Isolation | ideally a sandboxed worker process, not the ingest service |

---

### SH-8 — Word and Excel are not supported, and that is deliberate
**Severity: scope control**

The stated requirement includes Word and Excel. Today's supported extensions
are `.md .txt .markdown .pdf .csv .tsv .py .sh .json .yaml .yml .sql` and image
types. `docs/ARCHITECTURE_STATUS.md` explicitly lists *"DOCX … as supported"*
under **prohibited claims in active guidance**.

Adding Office formats is a real ingestion project, not a health-check feature:
they are ZIP+XML containers; tables and charts do not linearize into text
cleanly; and an `.xlsx`'s meaning frequently lives in formulas or an embedded
chart image that carries no text at all — a document that is *legitimately*
text-poor and would trip the SH-1 thresholds.

**Solution.** Keep it a separate epic with its own parser work and its own
per-type fitness thresholds. Do not let it ride along inside source health, or
both will slip.

---

### SH-9 — A false-positive quarantine deletes real evidence
**Severity: medium (safety of the feature itself)**

Any automated fitness threshold will eventually be wrong. In this system a false
positive is expensive and asymmetric: wrongly quarantining a good source removes
evidence that wiki pages cite, breaking address verification and the citation
chain. A legitimately short document — a one-line config, a brief memo — is
exactly the shape that trips a "too little text" rule.

**Solution.**
- Quarantine must be **reversible** and **listed**, never silent.
- Provide an explicit per-file override that survives re-scans (an allowlist
  entry beside `raw/.acl.yaml`, with a reason recorded).
- Prefer **flag-and-report** over auto-remove for class C (insufficient). Reserve
  automatic action for classes A/B/D, where the parser or the model gave an
  unambiguous failure.

---

### SH-10 — "Automatically fix corrupted data" is mostly not possible
**Severity: expectation setting**

There is nothing to reconstruct a damaged PDF *from*. The realistic protocol is
**quarantine → diagnose → restore or replace**, and only the middle step is
fully automatable.

**Solution, and an asset that already exists:** `raw/` is under Git. For class E
(changed since ingest — bit rot, partial write, bad sync),
`git checkout raw/<file>` restores the last known-good bytes **exactly**. This
is a real, safe, automatable repair. It also fails loudly for a file that was
never good — such as both motivating files, whose committed bytes are the bad
bytes — which is the correct outcome.

**Governance.** Apply the auto-healer's existing rule (R-6.4): **quarantine
automatically, repair only via Pull Request.** Automated replacement of evidence
is precisely where a human signature belongs.

---

## 4. Assessment of the proposed two-layer design

### Layer 1 — scheduled and on-upload health checks

**`fresh` (validate on upload): endorsed, and smaller than expected.** Validating
at the boundary is the right instinct, and `--dry-run` already does most of the
work (SH-6). This should be built first.

**`daily` (scheduled sweep): endorsed, with the cost gate from SH-5 as a
precondition.** An unconditional daily rescan does not survive contact with a
1 GB corpus and a rate-limited vision model. Hash-gated, it is cheap and
correct.

**On "must handle 1 GB and many types":** the binding constraints are not
throughput but (a) the paid model calls of class D, (b) the hostile-input
controls of SH-7, and (c) parser coverage — which does not currently include
Word or Excel (SH-8).

### Layer 2 — detecting damage between scheduled runs

The stated concern is: a healthy file becomes damaged, no scheduled scan has
run, and someone uses it.

**One correction to the premise.** Because Scout serves chunks from PostgreSQL
and never reads `raw/`, a damaged file does **not** break the query. Retrieval
still returns correct text. So the requirement *"warn that this file just got
corrupted, cannot be used"* does not describe what actually happens. The real
exposure is SH-3: **provenance silently becomes false while everything appears
to work.** That is a citation-integrity problem, not an availability problem,
and it is the more dangerous of the two.

**The good news:** this layer is mostly not new. `sync-job` already watches
`raw/` via `watchfiles` and reacts within seconds. Three additions complete it:

1. `content_sha256` on `rag_documents` — distinguishes *changed* from *changed
   and now broken* (SH-3).
2. Failure state that survives document purge (SH-4).
3. A quarantine status that reaches the agent at query time (SH-4) — the only
   genuinely new component, and the highest value.

---

## 5. Recommended build order

| # | Step | Unlocks | Size |
|---|---|---|---|
| 1 | Persist ingest outcomes independently of chunk rows | everything — nothing can report while failures are discarded | S |
| 2 | Add `content_sha256` + `validator_version` to `rag_documents` | SH-3, SH-5 | S |
| 3 | **Linter: `sources[].path` must resolve to ≥1 indexed chunk** | SH-2 — catches both motivating files | **XS** |
| 4 | `fresh` mode over `parse_file` / `--dry-run` + per-type thresholds | SH-1, SH-6 | M |
| 5 | `NO_EVIDENCE` + `SOURCE_QUARANTINED` statuses | SH-4 | S |
| 6 | Hostile-input controls (timeouts, ratio caps, XML hardening, sandbox) | SH-7 | M |
| 7 | `daily` mode, hash-gated | SH-5 | S |
| 8 | Quarantine list + override + Git-restore protocol | SH-9, SH-10 | M |
| 9 | Word / Excel ingestion | SH-8 | **separate epic** |

Step 3 is by a wide margin the highest value per unit of effort and does not
depend on steps 1 or 2.

---

## 6. Decisions required from the owner

1. **Per-type fitness thresholds.** What is the minimum acceptable yield for a
   PDF page, an image, a CSV, a code file? These are editorial judgements about
   the corpus, not engineering constants.
2. **Flag versus quarantine for class C.** Should an *insufficient* source be
   auto-removed, or only reported? (SH-9 recommends report-only.)
3. **Scope of Word/Excel.** Confirm it is a separate epic (SH-8), or accept that
   source health slips until parsers exist.
4. **Where the linter check runs.** Offline lint behind a flag, or alongside
   `verify_addresses.py` where live services are already required? (SH-2)
5. **The two motivating files.** Owner has stated the preference: replace the
   flawed test data rather than work around it. Both
   `raw/images/inference_dashboard.png` and
   `raw/reports/vllm_high_throughput_serving.pdf` need real content, and four
   wiki pages cite the latter as a technical report.

---

## 7. Related documents

- [`artifacts/superpowers/audit-2026-08-19-v2-system.md`](../artifacts/superpowers/audit-2026-08-19-v2-system.md)
  — the primary system audit; see its `FUTURE WORK` section, which first recorded
  SH-2 and SH-4 in brief.
- [`AGENTS.md`](../AGENTS.md) — frontmatter contract (R-1.4) and the injection
  guard (R-8.5) that constrain SH-4's status design.
- [`docs/ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md) — prohibited-claims
  list referenced by SH-8. **This document should be added to its active
  inventory.**
- [`docs/runbook.md`](runbook.md) — where the quarantine and Git-restore
  protocol of SH-10 should eventually be documented.
