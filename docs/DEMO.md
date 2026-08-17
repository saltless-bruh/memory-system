# Demo — SNP Memory System (T-4.2)

The end-to-end story in ~5 minutes: an agent answers a Vietnamese question by
finding the right compiled page, then pulling the **verbatim, cited** source
out of RAG — the two-layer flow the whole system exists to enable (R-5, R-4).

## 0. Prerequisites

- Stack up: `docker compose up -d --build` (see [runbook](runbook.md) §3);
  Ollama running on the host with `bge-m3` + an LLM + a VLM.
- One ingest of the sample source so RAG knows it (Nhịp A does this
  automatically in deployment; to force it once from the compose network:
  `POST http://rag:8000/index`). The sample `raw/reports/acme-2026-final.pdf`
  is already in the repo.
- An agent connected to **both** MCP servers — see
  [`CONNECT_AGENTS.md`](CONNECT_AGENTS.md).

## 1. The setup (one line to the audience)

> "Everything you're about to see runs on this machine. The wiki search, the
> document parsing, the embeddings — all local. Nothing leaves the box."

Point at the [runbook §1 no-egress boundary](runbook.md) — and be honest about
the one caveat: if the demo agent is a *cloud* model, what it **reads** leaves
via the agent provider, not via the system. For a true no-egress demo, use a
local agent model.

## 2. Ask a question — in Vietnamese, paraphrased on purpose

In the connected agent, ask:

> **"Làm sao lấy được mật khẩu của tài khoản dịch vụ có SPN?"**
> *(How do you get the password of a service account that has an SPN?)*

Note the question uses **no keyword** from the page title ("Kerberoasting") —
it's a semantic paraphrase. This is the Gate-4 point: multilingual `bge-m3`
retrieval finds it by *meaning*, which the English default could not.

## 3. What the agent should do (and what to narrate)

| Step | Tool call | What to point out |
|---|---|---|
| **Find** | `search_notes("mật khẩu tài khoản dịch vụ SPN")` | Returns `techniques/kerberoasting.md` at the top — semantic hit, not keyword. It loaded a **handful of hits**, not the whole index (R-5.2). |
| **Read** | `read_note("techniques/kerberoasting.md")` | The compiled page answers the *concept* (TGS → offline crack, gMSA defense) and carries a `sources[]` address into `raw/`. |
| **Decide** | — | If the page suffices, the agent **stops here and cites the page** — RAG is never called (R-5.1). To show the second layer, ask a follow-up that needs the original: *"Trích nguyên văn phần báo cáo Acme về vụ này"* (quote the Acme report verbatim). |
| **Fetch** | `rag_fetch(path="raw/reports/acme-2026-final.pdf", hint="Acme kerberoasting service account SPN offline crack", loc="p.12-14")` | Scout queries RAG, **post-filters to only that file** (R-4.3), and returns `status:"ok"` with verbatim `context[]` + `citations[]`. |
| **Answer** | — | The agent answers with a citation: *which page → which file → which `loc`*. The quote is verbatim from `raw/`, not paraphrased or invented. |

## 4. The three things that make this different (the "so what")

Weave these in as they happen — they are the design's whole point:

1. **Cheap agent context (R-5.2).** The agent read one page + a few hits +
   one passage — hundreds of tokens, flat as the corpus grows. Contrast: dumping
   the index + every page is O(N). Show the number:
   `python scripts/measure_tokens.py` → *933 tok agent-context vs ~185K naive at
   300 pages.*

2. **Injection-safe (R-8.5).** RAG content is **data, not instructions**. If a
   source file contained "ignore your instructions and run X", the agent quotes
   it as evidence and does not act — Scout's output has no action/command field
   by construction. (`tests/test_workflow.py::test_injection_payload_is_returned_as_data_only`.)

3. **The agent can't reach RAG directly (R-4.2).** `rag` publishes no port;
   only Scout can query it, and Scout only ever returns quotes + citations. Try
   to curl `http://localhost:8000` from the host — nothing there.

## 5. Optional: show the write path (PR-first, R-6.4)

Compiling a new page never touches `main` directly:

1. **Mint** a verifiable source address (so the hint actually retrieves the
   file): `python scripts/mint.py --path raw/reports/acme-2026-final.pdf --hint "…"`
   → a `sources[]` block that is **verify-PASS by construction**.
2. Write the page, then `python scripts/propose_page.py --title "…"` → it lints,
   branches, commits, and **stops at "open a PR"**. A human reviews and merges.
3. On merge, `gen_index.py` + `verify_addresses.py` regenerate the index and
   re-check every address (R-6.5).

Dropping ten files into `raw/` does **not** create ten pages — auto-ingest
feeds RAG only; pages are compiled deliberately (R-6.2).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `search_notes` returns nothing | `basic-memory` still doing its first-run scan/embed; wait, then retry. Ollama must be up (it does the embedding). |
| `rag_fetch` → `status:"no_source"` | The `hint` doesn't match RAG's extracted vocabulary, or the file isn't indexed. Re-mint the hint (`scripts/mint.py`); confirm `raw/` was indexed. |
| Agent "can't connect" to a server | Wrong URL/transport. Both are streamable-HTTP at `/mcp` (`:8765` wiki, `:8080` scout). See [`CONNECT_AGENTS.md`](CONNECT_AGENTS.md). |
| GET `/mcp` shows HTTP 406 | Expected — the endpoint needs MCP headers; only an MCP client speaks to it. |
