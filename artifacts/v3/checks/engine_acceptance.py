#!/usr/bin/env python3
"""Behavioural acceptance oracles for the engine's two claims.

Every other check in this scope measures shape: that a function exists, that a
contract file says the right thing, that a unit behaves against a fake. These
measure the two things the owner actually asks of the system.

  * **W-1** the agent queries the index and gets back an address and a file.
  * **W-2** a change in the vault reaches the index.

They run against the live stack through the shipped surfaces -- the production
`PgVectorRlsBackend`, the real corpus, the real gateway -- because the failures
worth catching here are the ones a double cannot reproduce. The prior
integration oracle modelled PostgreSQL in Python and could not have seen any of
them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scout.chunker import LiteLLMBatchEmbedder  # noqa: E402
from scout.diy_engine import ScoutDiyEngine  # noqa: E402
from scout.policy import CANONICAL_DEPARTMENTS  # noqa: E402
from scout.types import Scope  # noqa: E402

QUESTIONS = REPO_ROOT / "artifacts" / "v3" / "retrieval_questions.json"
VAULT = Path(
    os.environ.get("SNP_REFERENCE_VAULT")
    or Path.home() / "Documents" / "memo-project" / "Obsidian Vault"
)

#: Read from constants, never from the measured result. A floor derived from
#: what was observed is not a floor.
RECALL_AT_1_FLOOR = 0.60
RECALL_AT_5_FLOOR = 0.85
SEARCH_K = 5


class GateFailure(AssertionError):
    """A measured engine outcome did not hold."""


def require(condition: bool, message: str) -> None:
    """Raise a gate-specific failure when `condition` is false."""
    if not condition:
        raise GateFailure(message)


@dataclass(frozen=True)
class Question:
    lang: str
    query: str
    expect: str
    control: str | None = None


def load_questions() -> tuple[list[Question], list[Question]]:
    """Return the measurable questions and the controls, separately."""
    raw = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    require(isinstance(raw, list) and bool(raw), f"{QUESTIONS}: empty question set")
    parsed = [
        Question(
            lang=str(e["lang"]),
            query=str(e["query"]),
            expect=str(e["expect"]),
            control=str(e["control"]) if "control" in e else None,
        )
        for e in raw
    ]
    measurable = [q for q in parsed if q.control is None]
    controls = [q for q in parsed if q.control == "absent"]
    require(
        len(measurable) >= 40,
        f"question set has {len(measurable)} measurable entries, expected at least 40",
    )
    require(
        bool(controls),
        "no absent-page control in the question set; this gate could not tell "
        "present from absent",
    )
    languages = {q.lang for q in measurable}
    require(
        "vi" in languages,
        "no Vietnamese questions: a dense-timeout regression once made every "
        "Vietnamese query return nothing while every mechanical gate stayed green",
    )
    return measurable, controls


def _build_engine() -> ScoutDiyEngine:
    """The shipped engine, on the production backend. No doubles."""
    embedder = LiteLLMBatchEmbedder(
        base_url=os.environ.get("LITELLM_BASE_URL"),
        api_key=os.environ.get("LITELLM_MASTER_KEY"),
    )
    return ScoutDiyEngine.from_vault(embedder, wiki_dir=VAULT)


def _headings(page: object) -> list[str]:
    """The headings a citation could name, from the page's own outline."""
    outline = getattr(page, "outline", ()) or ()
    found: list[str] = []
    for entry in outline:
        if isinstance(entry, dict):
            heading = entry.get("heading") or entry.get("title") or entry.get("loc")
            if isinstance(heading, str) and heading.strip():
                found.append(heading.strip())
    return found


async def _find_read_cite() -> str:
    engine = _build_engine()
    scope = Scope(departments=frozenset(CANONICAL_DEPARTMENTS))
    measurable, controls = load_questions()

    ranks: list[int | None] = []
    returned_paths: set[str] = set()
    try:
        for question in measurable:
            hits = await engine.wiki_search(question.query, k=SEARCH_K, scope=scope)
            paths = [hit.path for hit in hits]
            returned_paths.update(paths)
            ranks.append(
                paths.index(question.expect) + 1 if question.expect in paths else None
            )

        # The control decides whether any of the above means anything. An
        # oracle that scores a page nobody has as "found" is measuring its own
        # optimism.
        for control in controls:
            hits = await engine.wiki_search(control.query, k=SEARCH_K, scope=scope)
            found = [hit.path for hit in hits]
            require(
                control.expect not in found,
                f"the absent-page control was scored as found ({control.expect}); "
                "this gate cannot tell present from absent",
            )

        require(
            bool(returned_paths),
            "no search returned any page at all; the gate cannot observe its subject",
        )

        # Every page search offered must actually be readable. This is the
        # check that would have caught the 431-versus-7 vault fork, where
        # search returned five correct identities and read raised KeyError on
        # every one of them: find and read disagreed about what the vault held.
        unreadable: list[str] = []
        uncitable: list[str] = []
        for path in sorted(returned_paths):
            try:
                page = await engine.wiki_read(path, mode="full", scope=scope)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                unreadable.append(f"{path}: {type(exc).__name__}: {exc}")
                continue
            if not (getattr(page, "body", "") or "").strip():
                unreadable.append(f"{path}: read returned an empty body")
                continue
            headings = _headings(page)
            if not headings:
                uncitable.append(path)
                continue
            # An answer cites a heading; that heading has to exist in the page
            # it claims to come from, or the citation is unverifiable.
            body = page.body
            missing = [h for h in headings if h not in body]
            if missing:
                uncitable.append(
                    f"{path}: outline names {missing[:2]} absent from body"
                )
    finally:
        await engine.aclose()

    require(
        not unreadable,
        f"{len(unreadable)} of {len(returned_paths)} returned pages could not be "
        f"read: {unreadable[:3]}",
    )
    require(
        not uncitable,
        f"{len(uncitable)} returned pages cannot be cited by heading: {uncitable[:3]}",
    )

    def found_at(n: int) -> float:
        return sum(1 for r in ranks if r is not None and r <= n) / len(ranks)

    at1, at5 = found_at(1), found_at(SEARCH_K)
    require(
        at1 >= RECALL_AT_1_FLOOR,
        f"recall@1 {at1:.2f} < {RECALL_AT_1_FLOOR:.2f}",
    )
    require(
        at5 >= RECALL_AT_5_FLOOR,
        f"recall@{SEARCH_K} {at5:.2f} < {RECALL_AT_5_FLOOR:.2f}",
    )
    for lang in sorted({q.lang for q in measurable}):
        subset = [r for q, r in zip(measurable, ranks, strict=True) if q.lang == lang]
        lang_at5 = sum(1 for r in subset if r is not None and r <= SEARCH_K) / len(
            subset
        )
        require(
            lang_at5 >= RECALL_AT_5_FLOOR,
            f"{lang} recall@{SEARCH_K} {lang_at5:.2f} < {RECALL_AT_5_FLOOR:.2f}",
        )

    print(
        f"  {len(measurable)} questions, {len(returned_paths)} distinct pages "
        f"returned and read; recall@1={at1:.2f} recall@{SEARCH_K}={at5:.2f}; "
        f"{len(controls)} absent-page control(s) correctly missed"
    )
    return "FIND READ CITE VERIFIED"


def group_find_read_cite() -> str:
    """Search finds the page, read returns its body, and the citation resolves."""
    return asyncio.run(_find_read_cite())


GROUPS: dict[str, Callable[[], str]] = {
    "find-read-cite": group_find_read_cite,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=sorted(GROUPS), required=True)
    args = parser.parse_args(argv)
    try:
        print(GROUPS[args.group]())
    except GateFailure as exc:
        print(f"FAIL [{args.group}] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
