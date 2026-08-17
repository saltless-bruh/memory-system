#!/usr/bin/env python3
"""GATE 4 — Vietnamese recall spike (T-0.7 → R-8.4.4, R-2.3, R-2.6).

Question: does the wiki-search embedding model retrieve the right page for
Vietnamese questions — including paraphrases that share no keywords with
the page? Measures recall@k for two embedding backends so the wiki-search
model choice is evidence-based:

  A. FastEmbed default  — basic-memory's in-process model (R-8.2).
  B. bge-m3 via LiteLLM  — the multilingual model already used for RAG.

If A's paraphrase recall is poor and B's is good, the decision is to point
wiki-search at bge-m3 (unify on one model, design.md §2.2).

This harness embeds each page's `summary` (the routing vector per §2.5) and
each question, then ranks pages by cosine similarity — mirroring how
Scout-DIY routes (design.md §2.2). It is intentionally a *retrieval* test
on summaries, not a full basic-memory search, so both backends are compared
on equal footing.

Backends are optional and auto-skipped if unavailable, so you can run
whichever is installed:
    python run_gate4.py                    # tries both, reports what ran
    python run_gate4.py --backends bge-m3  # only the LiteLLM route
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "out"
sys.path.insert(0, str(REPO_ROOT / "spikes" / "_lib"))
import vault  # noqa: E402

K_VALUES = (1, 3, 5)


# ── cosine without numpy dependency ──────────────────────────────────
def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ── Backend A: FastEmbed (in-process, what basic-memory uses) ────────
def embed_fastembed(texts: list[str], model: str | None):
    from fastembed import TextEmbedding  # type: ignore

    name = model or "BAAI/bge-small-en-v1.5"  # FastEmbed default family
    emb = TextEmbedding(model_name=name)
    return [list(v) for v in emb.embed(texts)], name


# ── Backend B: bge-m3 via LiteLLM (OpenAI-compatible /embeddings) ────
def embed_litellm(texts: list[str], model: str):
    import urllib.request

    base = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    key = os.environ.get("LITELLM_MASTER_KEY", "sk-local-dev-change-me")
    body = json.dumps({"model": model, "input": texts}).encode()
    req = urllib.request.Request(
        f"{base}/v1/embeddings",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
    return vecs, model


BACKENDS = {
    "fastembed": lambda texts: embed_fastembed(
        texts, os.environ.get("FASTEMBED_MODEL")
    ),
    "bge-m3": lambda texts: embed_litellm(
        texts, os.environ.get("SNP_EMBED_MODEL", "snp-embed")
    ),
}


def evaluate(pages, questions, vecs_pages, vecs_q):
    """Return per-question ranks + aggregate recall@k."""
    slugs = [p.slug for p in pages]
    per_q = []
    hits = {k: 0 for k in K_VALUES}
    para_total = 0
    para_hits = {k: 0 for k in K_VALUES}

    for qi, item in enumerate(questions):
        sims = sorted(
            (
                (cosine(vecs_q[qi], vecs_pages[pi]), slugs[pi])
                for pi in range(len(pages))
            ),
            reverse=True,
        )
        ranked = [slug for _, slug in sims]
        acceptable = {item["target"], *item.get("also_acceptable", [])}
        rank = next((i + 1 for i, s in enumerate(ranked) if s in acceptable), None)
        is_para = bool(item.get("paraphrase"))
        if is_para:
            para_total += 1
        for k in K_VALUES:
            if rank is not None and rank <= k:
                hits[k] += 1
                if is_para:
                    para_hits[k] += 1
        per_q.append(
            {
                "q": item["q"],
                "target": item["target"],
                "paraphrase": is_para,
                "rank": rank,
                "top3": ranked[:3],
            }
        )

    n = len(questions)
    recall = {f"@{k}": round(hits[k] / n, 3) for k in K_VALUES}
    recall_para = {
        f"@{k}": round(para_hits[k] / para_total, 3) if para_total else None
        for k in K_VALUES
    }
    return {
        "recall": recall,
        "recall_paraphrase_only": recall_para,
        "n_questions": n,
        "n_paraphrase": para_total,
        "per_question": per_q,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--backends", nargs="*", default=list(BACKENDS), choices=list(BACKENDS)
    )
    ap.add_argument("--testset", default=str(Path(__file__).parent / "testset.yaml"))
    args = ap.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    testset = yaml.safe_load(Path(args.testset).read_text(encoding="utf-8"))
    questions = testset["questions"]
    pages = vault.load_pages()
    # Guard: every target must resolve to a real page.
    slugs = {p.slug for p in pages}
    bad = {item["target"] for item in questions} - slugs
    if bad:
        print(f"FAIL: testset references unknown page slugs: {sorted(bad)}")
        return 2

    page_summaries = [p.frontmatter.get("summary", "") for p in pages]
    q_texts = [item["q"] for item in questions]

    report = {"gate": 4, "when": stamp, "n_pages": len(pages), "backends": {}}
    print(
        f"Gate 4: {len(pages)} pages, {len(questions)} questions "
        f"({sum(1 for q in questions if q.get('paraphrase'))} paraphrase)\n"
    )

    for name in args.backends:
        print(f"=== backend: {name} ===")
        try:
            t0 = time.time()
            vecs_pages, model_used = BACKENDS[name](page_summaries)
            vecs_q, _ = BACKENDS[name](q_texts)
            res = evaluate(pages, questions, vecs_pages, vecs_q)
            res["model"] = model_used
            res["secs"] = round(time.time() - t0, 2)
            report["backends"][name] = res
            print(f"  model={model_used}  recall={res['recall']}")
            print(f"  paraphrase-only recall={res['recall_paraphrase_only']}")
            misses = [
                q for q in res["per_question"] if q["rank"] is None or q["rank"] > 3
            ]
            if misses:
                print(f"  {len(misses)} miss@3:")
                for m in misses:
                    print(
                        f"    - [{m['target']}] rank={m['rank']}  "
                        f"top3={m['top3']}  «{m['q'][:60]}»"
                    )
        except ImportError as e:
            print(f"  SKIP ({name}): missing dependency — {e}")
            report["backends"][name] = {"skipped": f"ImportError: {e}"}
        except Exception as e:  # noqa: BLE001 — spike: capture & continue
            print(f"  SKIP ({name}): {type(e).__name__}: {e}")
            report["backends"][name] = {"skipped": f"{type(e).__name__}: {e}"}
        print()

    out = OUT_DIR / f"gate4_{stamp}.json"
    if vault.write_transcript(out, report):
        print(f"Transcript → {out.relative_to(REPO_ROOT)}")
    print(
        "NEXT: compare paraphrase-only recall between backends; record the "
        "wiki-search embedding decision in spikes/GATE_RESULTS.md."
    )

    ran = [b for b, r in report["backends"].items() if "skipped" not in r]
    return 0 if ran else 1


if __name__ == "__main__":
    raise SystemExit(main())
