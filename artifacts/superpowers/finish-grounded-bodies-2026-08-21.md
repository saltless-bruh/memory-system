# Finish: Grounded Page Bodies for `compile_note` (single-page case)

Branch: `fix/architecture-security-hardening` · Plan: `artifacts/superpowers/plan.md`
Completed: 2026-08-21

## What changed

`compile_note` no longer emits a templated body. It generates the
`## Technical Specifications` section from the passages the page's minted address
retrieves, and refuses to write a page that fails its own groundedness check.

The load-bearing fix was a corpus mismatch. Generation read
`_bounded_document_text()` (first 12k chars of the parsed file) while the judge read the
top-20 chunks `rag_fetch` returns for the minted address under the page's department.
Generation now happens **after** minting, from `collect_context`'s output — the verifier's
own function — so both read one corpus.

New pipeline: parse → call A (summary/entities/hint) → mint → `collect_context` →
call B (prose from those passages) → lint → self-judge → atomic write.

Also removed: the `Key entities:` line (duplicating frontmatter as prose gives the judge a
claim with no source) and the provenance sentence "Compiled from X through the validated
model-and-mint pipeline" (a claim about our tooling that no passage supports).

## Verification

| Check | Result |
|---|---|
| `ruff check .` | All checks passed |
| `mypy scout scripts` | Success, 49 source files |
| `pytest -q` | **686 passed**, 21 errors |
| `verify_addresses.py` (live) | exit 0 — **2 PASS**, 0 FAIL, 0 DRIFT |
| `verify_groundedness.py` (live) | exit 0 — **GROUNDED**, 20 passages each |

The 21 errors are the live-integration env gate (`SNP_INTEGRATION_PROJECT`, `POSTGRES_*`
unset in this shell), identical at HEAD. `ruff format --check .` reports 64 files, also
identical at HEAD via `git stash`, and neither changed file is among them — not reformatted.

**Live end-to-end**, against the running stack and the real 26-page paper:
- `wiki/concepts/advantages-and-disadvantages-of-deep-learning.md` — 6 paragraphs
- `wiki/concepts/convolutional-neural-networks.md` — 6 paragraphs including the layer
  dimensions (`m x m x r`), the activation form `hk = f(Wk * x + bk)`, and the attribution
  to Goodfellow et al.

Where the old template produced two bullet points, these are compiled knowledge.

**Independent spot-check.** The judge is the same model that wrote the prose, so its
verdict is weak evidence. I grepped the parsed source for the oddest-reading claim —
"deep learning often only needs millions of data points" — and it is **verbatim** from the
paper. The awkward phrasing is the author's, not a distortion.

## Review pass

**Blocker** — none.

**Major — the judge is the same model as the generator.** `LITELLM_JUDGE_MODEL` is unset,
so the judge defaults to `snp-llm`, which this deployment routes to the same
`gemini/gemini-3.5-flash` as `LITELLM_LLM_MODEL`. Self-preference bias is measured, not
hypothetical. The gateway currently exposes only Gemini routes, so genuine independence
needs a second provider key, not just a config change. Compile prints both model names to
stderr so a same-model run is visible. **This is the weakest joint in the design.**

**Major — call A's generated hint is a real failure mode.** The first live compile failed
at mint: the model's hint did not clear the rank-1 + 50%-grounding gate, and neither did
the title fallback. `scripts/mint.py` mints a hand-picked hint for the same file and loc,
so this is pre-existing P4, not a regression — but it means compile fails outright on some
titles with a terse message. Worth widening the candidate-hint list.

**Minor — judging re-retrieves.** `verify_page` fetches context again rather than reusing
what generation used, so each attempt does two retrievals. Deliberate: it makes "the
self-check IS the merge gate" literally true rather than approximately. DB work, not model
cost.

**Minor — `_JSON_SCHEMA_UNSUPPORTED` never re-probes.** A model that 400s once is
downgraded to `json_object` for the process lifetime. Correct for a CLI; for the planned
long-lived MCP server it becomes a permanent downgrade that survives a gateway fix.

**Nit** — two pages written into `wiki/` by live testing remain uncommitted; keep or
delete as you prefer.

**Nit** — `sync-job` was `restarting` during this session while the other six services were
healthy. Unrelated to this change, but it was 7/7 earlier.

## Corrections made during execution

- **`MIN_BODY_SPECIFICATIONS = 2` was wrong in my plan.** A floor of two paragraphs forces
  padding when a source supports only one claim — the exact fabrication pressure this work
  exists to remove. Lowered to 1; the test caught it.
- **BP-1/BP-2 were applied to call B only at first.** The plan said both calls. Call A now
  uses the same nonce fence and `json_schema` path; its delimiter test was updated to
  assert the nonce fence instead of the spoofable static tag.
- **`GroundednessError` escaped `main()`.** An unconfigured judge would have produced a
  traceback instead of a clean error. Translated to `CompileNoteError`, with a regression
  test.

## Follow-ups

1. Set `LITELLM_JUDGE_MODEL` to a different model family before trusting the self-check.
2. Widen call A's candidate hints so minting fails less often.
3. Step 3 plan: P2 (decomposition), P3 (two-pass slugs), batch-P4 (pre-flight minting of
   all N addresses before any write), P5 (cross-file atomicity) — with the cost numbers
   this work produced: 2 generations + 1 judge per page, plus 2 retrievals per attempt.
