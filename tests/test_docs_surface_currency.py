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
#: `dual-layer` and `source evidence` are not surface names but phrases that
#: name the retired two-tier workflow itself -- "search the wiki, then fetch
#: source evidence from a second RAG tier" -- which a doc can assert without
#: naming any single retired tool. `docs/DEMO.md`'s title and opening sentence
#: did exactly that (fix round 1) while the rest of the file, twelve lines
#: later, correctly described the real single-tier flow.
RETIRED_SURFACES = (
    "rag_fetch",
    "basic-memory",
    "search_notes",
    "read_note",
    "auto-heal",
    "snp-rag-fetch",
    "snp-search-wiki",
    "dual-layer",
    "source evidence",
    # The 2026-09 retirements. Until these were added the test was green while
    # every one of them was still asserted as current somewhere it could not
    # see, which is the failure this file was written to prevent.
    #
    # 016ed43 (leaf-4.3) deleted the local stdio MCP server and with it the
    # `snpmemory mcp` command; `snpmemory mcp-config` is live and is not
    # matched (see `_mentions`).
    # Bare "stdio" is not retired: the Scout bridge a VS Code client launches
    # is `type: "stdio"`. The retired thing is a stdio *server* of our own.
    "stdio server",
    "local stdio",
    "local, stdio",
    "stdio connection",
    "snpmemory mcp",
    # 29f1f50 deleted .gitea/workflows/auto-healer.yaml; `auto-heal` alone
    # never matched it, because a boundary does not fall inside "healer".
    "auto-healer",
    # Named in README and DEMO as a gate to run; no such script exists.
    "ci_address_gate",
    # a841912 began serving wiki_quote, so there are three retrieval tools and
    # source extraction is no longer deferred.
    "two retrieval tools",
    "exactly two tools",
    "extraction is deferred",
    # packages/snp-agent/skills/ ships ten skills.
    "seven skills",
)

#: Docs a reader, or an agent, is expected to act on. The entry docs and the
#: shipped agent package are the ones agents load as instructions, so a stale
#: claim there is acted on rather than merely read.
CHECKED_DOCS = (
    "docs/DEMO_OPENCODE.md",
    "README.md",
    "docs/DEMO.md",
    "docs/ARCHITECTURE_STATUS.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/CONNECT_AGENTS.md",
    "docs/CLI_SPEC.md",
    "docs/runbook.md",
    *sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "packages" / "snp-agent").rglob("*")
        if path.is_file() and path.suffix in {".md", ".json"}
    ),
)


#: A sentence may name a retired surface when it is explicitly marking it as
#: gone. "basic-memory was replaced by Scout" is accurate documentation;
#: "basic-memory reads /vault-replica read-only" is a lie. The distinction is
#: the marker, so the test looks for one rather than banning the word outright.
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
    "before v3",
    "v2 ",
    "deleted",
    "is gone",
    "existed until",
    "used to",
    # A dated measurement reports what was true then, not what is served now.
    "measured on",
)


def _mentions(name: str, text: str) -> bool:
    """`name` appears as a whole token, not as the stem of a longer one.

    A hyphen counts as part of a token, so `snpmemory mcp` does not fire on
    the live `snpmemory mcp-config` and `auto-heal` does not fire on
    `auto-healer`; a leading dot marks a qualified Python name, so the live
    internal function `scout.core.rag_fetch` is not the retired MCP tool.
    """
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", text) is not None


#: A block starts at a blank line, a heading, a list item or a table row, so
#: one list item's marker never excuses its neighbour.
_BLOCK_START = re.compile(r"^\s*(#|[-*+] |\d+\. |\|)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _stale_mentions(text: str) -> list[tuple[int, list[str]]]:
    """(line, names) for each sentence presenting a retired surface as current.

    The marker is judged per sentence, not per physical line: prose wraps, and
    both "…a local stdio server exposing X. It is deleted." (history, marker in
    the next sentence) and "Two MCP servers: … `snpmemory` (local, stdio). The
    retired container is absent." (stale, marker in an unrelated sentence on
    the same line) defeated a line-level check in opposite directions. A block
    whose first sentence is marked -- "A second surface existed until
    2026-09-24." -- is history throughout.
    """
    blocks: list[list[tuple[int, str]]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            blocks.append([])
            continue
        if not blocks or _BLOCK_START.match(line):
            blocks.append([])
        blocks[-1].append((number, line.strip()))

    found: list[tuple[int, list[str]]] = []
    for block in blocks:
        if not block:
            continue
        joined = ""
        starts: list[tuple[int, int]] = []
        for number, line in block:
            starts.append((len(joined), number))
            joined += line.lower() + " "
        offset = 0
        for index, sentence in enumerate(_SENTENCE_END.split(joined)):
            begin = joined.index(sentence, offset)
            offset = begin + len(sentence)
            marked = any(marker in sentence for marker in HISTORICAL_MARKERS)
            if marked and index == 0:
                break
            if marked:
                continue
            named = sorted(n for n in RETIRED_SURFACES if _mentions(n, sentence))
            if named:
                # Report the line the first retired name sits on, not the
                # line the sentence happens to start on.
                at = begin + min(sentence.index(n) for n in named)
                found.append((max(n for start, n in starts if start <= at), named))
    return found


def test_the_matcher_tells_a_retired_name_from_a_live_longer_one() -> None:
    assert _mentions("snpmemory mcp", "run `snpmemory mcp` to serve stdio")
    assert not _mentions("snpmemory mcp", "run `snpmemory mcp-config --client x`")
    assert _mentions("auto-healer", ".gitea/workflows/auto-healer.yaml")
    assert not _mentions("auto-heal", ".gitea/workflows/auto-healer.yaml")
    assert not _mentions("rag_fetch", "`fetch` calls `scout.core.rag_fetch`")


def test_the_marker_is_judged_per_sentence_not_per_line() -> None:
    stale_beside_unrelated_marker = (
        "Two MCP servers: `scout` and `snpmemory`\n"
        "(local, stdio). The retired container is absent.\n"
    )
    history_marked_next_sentence = (
        "**A second surface existed until 2026-09-24.** `snpmemory mcp` was\n"
        "a local stdio server. It is deleted, not mitigated.\n"
    )
    marked_list_item_beside_stale_one = (
        "- the stdio server was deleted on 2026-09-24\n"
        "- run `snpmemory mcp` for local verification\n"
    )

    assert _stale_mentions(stale_beside_unrelated_marker) == [(2, ["local, stdio"])]
    assert _stale_mentions(history_marked_next_sentence) == []
    assert _stale_mentions(marked_list_item_beside_stale_one) == [
        (2, ["snpmemory mcp"])
    ]


@pytest.mark.parametrize("relative", CHECKED_DOCS)
def test_no_shipped_doc_names_a_retired_surface(relative: str) -> None:
    """Flag only sentences that present a retired surface as current."""
    lines = (REPO_ROOT / relative).read_text(encoding="utf-8").splitlines()
    offenders = [
        f"{relative}:{number} {named}: {lines[number - 1].strip()[:70]}"
        for number, named in _stale_mentions("\n".join(lines))
    ]
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
