# Retrieval baseline — before and after lifting the reference list

**Date:** 2026-08-25 · Plan step 8/9 · Corpus:
`raw/papers/computers-12-00091.pdf` (the only source in `raw/`)

The question this answers: **is a reranker needed?** The 2026 literature calls a
cross-encoder reranker "the cheapest, highest-leverage fix for RAG systems where
vector search returns the right answer somewhere in the top 50 but rarely at
rank 1" — which was this system's exact symptom. But the measurement said 18% of
the index was bibliography, which is removable structurally at zero model cost.
So the reranker decision waits for the numbers after that is gone.

---

## The measurement

Each of the five compiled pages is asked for **its own minted hint**, through
`snpmemory fetch` — the same path `rag_fetch` serves to an agent. Rank 1 is
either the prose the page is about, or it is not.

| Page | Before | After |
| --- | --- | --- |
| `advantages-and-disadvantages-of-deep-learning` | prose | prose |
| `convolutional-neural-networks` | **bibliography** — `ACM 2017, 60, 84–90. [CrossRef] 50. Hinton, G.E. Deep belief networks` | **prose** — `…provides the actual outputs ỹ. Convolutional Neural Networks…` |
| `natural-language-processing` | prose | prose |
| `recognition-of-objects-in-images` | prose | prose |
| `the-key-distinctions-between-deep-learning-and-machine-learning` | **front matter** — `Citation: Taye, M.M. Understanding of Machine Learning with Deep Learning…` | **front matter** — unchanged |

**3 of 5 → 4 of 5** correct at rank 1.

### Corpus effect

| | Before | After |
| --- | --- | --- |
| Parsed text | 109,457 chars | 92,008 chars |
| Chunks containing `[CrossRef]` / `doi.org` / `[PubMed]` | 23 | 2 |
| References in document metadata | 0 | 87 |

The 2 remaining are false positives of the detector, not bibliography: the
paper's own `Citation:` front-matter block, and one prose chunk that mentions a
DOI.

---

## What the remaining failure actually is

`the-key-distinctions-…` did not improve, and **a reranker would not fix it
either.** Its minted hint is:

> `Understanding of Machine Learning with Deep Learning: Architectures, Workﬂow, Applications and Future Directions by Mohammad Mustafa Taye`

That is **the paper's own title**. The chunk it retrieves — the paper's
`Citation:` block — is the single best lexical *and* semantic match for that
string in the entire document. Retrieval is behaving correctly; the hint is
pointing at the paper rather than at the section the page is about.

This is an **authoring** defect, not a retrieval one, and it has a known cause:
T4.1 records that two of the five pages were compiled from a **hand-edited**
plan. `verify-addresses` still reports it PASS, correctly — the address does
retrieve its own file at top rank, which is all the address contract claims. The
address contract and retrieval *quality* are different questions, and this page
is the case that separates them.

---

## Decision: no reranker

Recommended, on this evidence:

1. **The measured problem was noise, and the noise is gone.** 18% of the index
   was a different kind of object indexed as prose. Removing it fixed the one
   genuine ranking failure at zero model cost and zero added latency.
2. **The one remaining case is not a ranking problem.** Nothing a cross-encoder
   does would make a section outrank the paper's own citation block for a query
   that *is* the paper's title. The fix is to re-mint that page's hint against
   the section it describes — cheap, and it belongs with T4.1's recompile.
3. **A reranker is not free here.** LiteLLM's rerank support does not cover this
   stack's providers (Gemini for embeddings, OpenRouter for chat), so it would
   mean either a new paid provider or a local cross-encoder — a large dependency
   for a corpus of one document. The alternative that needs no new provider is
   an LLM-as-reranker over the top-N through the existing `snp-llm` route, which
   costs a model call per retrieval on the hot path.

**Revisit when:** the corpus holds several documents and rank-1 correctness is
measured below ~80% on a sample of real hints, or when a second source shows the
same failure with a *good* hint. One document is not a sample, and this record
exists so the next measurement has something to compare against.
