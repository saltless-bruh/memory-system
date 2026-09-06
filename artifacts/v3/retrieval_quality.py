#!/usr/bin/env python
"""Measure whether wiki_search actually finds the right page.

Every other V3 gate measures plumbing: that chunks carry text, that results are
distinct pages, that a dead embedder degrades. None of them asks the only
question the owner will ask -- *did it find my article?*  This one does.

The question set pairs a natural-language query with the page that must come
back. Vietnamese entries are load-bearing: the reference corpus is authored by a
Vietnamese speaker who queries in Vietnamese, and a 250 ms dense timeout once
made every such query return nothing while every mechanical gate stayed green.

Usage:
    python artifacts/v3/retrieval_quality.py                 # report
    python artifacts/v3/retrieval_quality.py --gate          # exit non-zero below floor
    python artifacts/v3/retrieval_quality.py --questions FILE
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scout.chunker import LiteLLMBatchEmbedder  # noqa: E402
from scout.diy_engine import ScoutDiyEngine  # noqa: E402
from scout.types import Scope  # noqa: E402

DEFAULT_QUESTIONS = Path(__file__).with_name("retrieval_questions.json")
CANONICAL_DEPARTMENTS = frozenset({"redteam", "blueteam", "ai_eng", "infra"})

# A retrieval system that cannot put the right page in the top five is not
# usable by an agent that only reads the top few hits.
RECALL_AT_1_FLOOR = 0.60
RECALL_AT_5_FLOOR = 0.85


@dataclass(frozen=True)
class Question:
    lang: str
    query: str
    expect: str


@dataclass
class Outcome:
    question: Question
    ranked: list[str]

    @property
    def rank(self) -> int | None:
        for position, path in enumerate(self.ranked, 1):
            if path == self.question.expect:
                return position
        return None


def load_questions(path: Path) -> list[Question]:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"{path}: expected a non-empty JSON list")
    questions = [
        Question(lang=str(e["lang"]), query=str(e["query"]), expect=str(e["expect"]))
        for e in raw
    ]
    missing = [q.expect for q in questions if not (VAULT / q.expect).exists()]
    if missing:
        raise SystemExit(
            "question set names pages that are not in the vault: "
            + ", ".join(sorted(set(missing))[:5])
        )
    return questions


VAULT = Path(
    os.environ.get("SNP_REFERENCE_VAULT")
    or Path.home() / "Documents" / "memo-project" / "Obsidian Vault"
)


async def measure(questions: list[Question], k: int) -> list[Outcome]:
    embedder = LiteLLMBatchEmbedder(
        base_url=os.environ.get("LITELLM_BASE_URL"),
        api_key=os.environ.get("LITELLM_MASTER_KEY"),
        # No model override: the benchmark has to embed its queries the way
        # the served backend does, or it is measuring a route nobody uses.
    )
    engine = ScoutDiyEngine.from_vault(embedder, wiki_dir=VAULT)
    scope = Scope(departments=CANONICAL_DEPARTMENTS)
    outcomes: list[Outcome] = []
    try:
        for question in questions:
            hits = await engine.wiki_search(question.query, k=k, scope=scope)
            outcomes.append(Outcome(question, [hit.path for hit in hits]))
    finally:
        await engine.aclose()
    return outcomes


def recall_at(outcomes: list[Outcome], n: int) -> float:
    if not outcomes:
        return 0.0
    hit = sum(1 for o in outcomes if o.rank is not None and o.rank <= n)
    return hit / len(outcomes)


def report(outcomes: list[Outcome]) -> None:
    for outcome in outcomes:
        rank = outcome.rank
        mark = "OK  " if rank == 1 else (f"@{rank}  " if rank else "MISS")
        print(f"  {mark} [{outcome.question.lang}] {outcome.question.query}")
        if rank != 1:
            print(f"        expected: {outcome.question.expect}")
            got = ", ".join(outcome.ranked[:3]) or "(nothing)"
            print(f"        returned: {got}")

    print()
    for lang in sorted({o.question.lang for o in outcomes}):
        subset = [o for o in outcomes if o.question.lang == lang]
        print(
            f"  {lang}: n={len(subset):3}  "
            f"recall@1={recall_at(subset, 1):.2f}  "
            f"recall@3={recall_at(subset, 3):.2f}  "
            f"recall@5={recall_at(subset, 5):.2f}"
        )
    print(
        f"  ALL: n={len(outcomes):3}  "
        f"recall@1={recall_at(outcomes, 1):.2f}  "
        f"recall@3={recall_at(outcomes, 3):.2f}  "
        f"recall@5={recall_at(outcomes, 5):.2f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()

    questions = load_questions(args.questions)
    outcomes = asyncio.run(measure(questions, args.k))
    report(outcomes)

    if not args.gate:
        return 0

    at1, at5 = recall_at(outcomes, 1), recall_at(outcomes, 5)
    failures = []
    if at1 < RECALL_AT_1_FLOOR:
        failures.append(f"recall@1 {at1:.2f} < {RECALL_AT_1_FLOOR:.2f}")
    if at5 < RECALL_AT_5_FLOOR:
        failures.append(f"recall@5 {at5:.2f} < {RECALL_AT_5_FLOOR:.2f}")
    # A language that silently returns nothing is the exact regression this
    # gate exists to catch, so no language may fall below the overall floor.
    for lang in sorted({o.question.lang for o in outcomes}):
        subset = [o for o in outcomes if o.question.lang == lang]
        if recall_at(subset, 5) < RECALL_AT_5_FLOOR:
            failures.append(
                f"{lang} recall@5 {recall_at(subset, 5):.2f} < {RECALL_AT_5_FLOOR:.2f}"
            )
    if failures:
        print("\nFAIL [retrieval-quality] " + "; ".join(failures))
        return 1
    print("\nRETRIEVAL QUALITY VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
