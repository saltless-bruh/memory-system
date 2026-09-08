"""Shipped documentation must not describe surfaces the system retired.

A manual grep missed this twice: once because it searched only prose and was
blind to a JSON example (`[]` at docs/DEMO_OPENCODE.md:276), once because a
fourth stale file was never in the sweep at all. A test does not get tired.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Names the system no longer exposes. `rag_fetch` and `basic-memory` were
#: retired in V3; `search_notes`/`read_note` were basic-memory's tools.
RETIRED_SURFACES = (
    "rag_fetch",
    "basic-memory",
    "search_notes",
    "read_note",
    "auto-heal",
    "snp-rag-fetch",
    "snp-search-wiki",
)

#: Docs a reader is expected to act on. Task 4 adds the entry docs.
CHECKED_DOCS = (
    "docs/DEMO_OPENCODE.md",
    "README.md",
    "docs/DEMO.md",
    "docs/ARCHITECTURE_STATUS.md",
)


#: A line may name a retired surface when it is explicitly marking it as gone.
#: "basic-memory was replaced by Scout" is accurate documentation; "basic-memory
#: reads /vault-replica read-only" is a lie. The distinction is the marker, so
#: the test looks for one rather than banning the word outright.
#: NOTE: "v2 " (trailing space) is unverified in isolation as a marker — no
#: live over-skip has been found for it, but it has not been exercised on its
#: own against a genuinely stale "v2 ..." line. Left as-is per review.
HISTORICAL_MARKERS = (
    "historical",
    "no longer",
    "was replaced",
    "retired",
    "removed in v3",
    "prior to v3",
    "v2 ",
)


@pytest.mark.parametrize("relative", CHECKED_DOCS)
def test_no_shipped_doc_names_a_retired_surface(relative: str) -> None:
    """Flag only lines that present a retired surface as current.

    Matches are anchored on word boundaries so a retired name never fires as
    a false positive inside an unrelated, currently-live token — e.g.
    `auto-heal` must not match `auto-healer.yaml`, a live CI config file.
    """
    offenders: list[str] = []
    for number, line in enumerate(
        (REPO_ROOT / relative).read_text(encoding="utf-8").splitlines(), start=1
    ):
        lowered = line.lower()
        if any(marker in lowered for marker in HISTORICAL_MARKERS):
            continue
        named = sorted(
            {
                name
                for name in RETIRED_SURFACES
                if re.search(rf"\b{re.escape(name)}\b", line)
            }
        )
        if named:
            offenders.append(f"{relative}:{number} {named}: {line.strip()[:70]}")
    assert not offenders, "retired surfaces presented as current:\n" + "\n".join(
        offenders
    )


@pytest.mark.parametrize("relative", CHECKED_DOCS)
def test_no_shipped_doc_shows_wiki_search_returning_a_bare_list(
    relative: str,
) -> None:
    """The prose sweep missed this because the offender was a code block.

    `wiki_search` returns an envelope, never a bare top-level array — empty
    (`[]`) or populated (`[{"path": "foo.md", ...}]`). A doc showing either
    shape as the response teaches a reader to index the result directly,
    which is exactly what broke three gate consumers. Each fenced `json`
    block is parsed and checked for a top-level `list`; blocks that fail to
    parse (illustrative fragments, `...` elisions) are skipped rather than
    treated as evidence of anything.
    """
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    fenced = re.findall(r"```json\s*\n(.*?)```", text, re.DOTALL)
    offenders: list[str] = []
    for block in fenced:
        stripped = block.strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            offenders.append(stripped[:70])
    assert not offenders, (
        f"{relative} shows wiki_search returning a bare top-level array; it "
        'returns {"results": [...], "returned": N, "suppressed_as_seen": N, '
        f'"has_more": bool}}:\n' + "\n".join(offenders)
    )
