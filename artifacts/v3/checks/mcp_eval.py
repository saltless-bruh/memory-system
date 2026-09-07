#!/usr/bin/env python3
"""Can an agent holding only the two served tools do real work?

`retrieval_quality.py` measures ranking and `engine_acceptance.py` measures
plumbing. Neither asks the question the mcp-builder guide's Phase 4 asks: given
`wiki_search` and `wiki_read` and nothing else, can an agent answer something a
person would actually ask?

This oracle replays that. For each pair in `artifacts/v3/evals/mcp_eval.xml` it
searches the served surface with the question as written, reads the pages the
search returned, and requires the recorded answer to appear in what came back.
It speaks only MCP over `SCOUT_URL` for exactly the reason the W-1 oracle does:
a check that could reach the vault another way would stop measuring the surface
an agent has.

Every pair was solved through this same surface before it was written down, so
a failure here is a regression in the system, not a question that was always
wrong. Four pairs are Vietnamese because a dense-timeout regression once
silenced every Vietnamese query while every mechanical gate stayed green.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The agent-facing surface, spoken to exactly as an agent would.
SCOUT_URL = os.environ.get("SNP_SCOUT_URL", "http://127.0.0.1:8080/mcp")
SCOUT_TOKENS = REPO_ROOT / ".secrets" / "scout_static_tokens.json"
EVAL_XML = REPO_ROOT / "artifacts" / "v3" / "evals" / "mcp_eval.xml"

#: What an agent asking one question would take. Reading the whole returned
#: page set is the honest budget: an agent that has to read the sixth hit to
#: answer has not really been routed there.
SEARCH_K = 5
#: The eval is a fixed set; a shrunken file is itself a regression.
EXPECTED_PAIRS = 10
#: The bilingual guarantee, asserted rather than assumed.
MIN_VIETNAMESE_PAIRS = 3

#: Characters that only appear in Vietnamese text, used to count the bilingual
#: half of the set without carrying a language tag in the eval file.
_VIETNAMESE_MARKS = (
    "ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
)


class GateFailure(AssertionError):
    """A measured engine outcome did not hold."""


def require(condition: bool, message: str) -> None:
    """Raise a gate-specific failure when `condition` is false."""
    if not condition:
        raise GateFailure(message)


@dataclass(frozen=True)
class QaPair:
    """One question and the answer string that must come back from the read."""

    index: int
    question: str
    answer: str

    @property
    def is_vietnamese(self) -> bool:
        lowered = self.question.lower()
        return any(mark in lowered for mark in _VIETNAMESE_MARKS)


def load_pairs(path: Path = EVAL_XML) -> list[QaPair]:
    """Read the eval file, failing the gate rather than the parser."""
    require(path.is_file(), f"no eval set at {path}")
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as exc:
        raise GateFailure(f"{path} is not well-formed XML: {exc}") from exc
    require(
        root.tag == "evaluation",
        f"{path}: root element is <{root.tag}>, expected <evaluation>",
    )
    pairs: list[QaPair] = []
    for index, node in enumerate(root.findall("qa_pair"), start=1):
        question = (node.findtext("question") or "").strip()
        answer = (node.findtext("answer") or "").strip()
        require(bool(question), f"{path}: qa_pair {index} has no <question>")
        require(bool(answer), f"{path}: qa_pair {index} has no <answer>")
        pairs.append(QaPair(index=index, question=question, answer=answer))
    return pairs


def _scout_token() -> str:
    """The widest-scoped static token, read at run time and never printed."""
    require(SCOUT_TOKENS.is_file(), f"no scout tokens at {SCOUT_TOKENS}")
    tokens = json.loads(SCOUT_TOKENS.read_text(encoding="utf-8"))
    require(bool(tokens), f"{SCOUT_TOKENS}: no tokens configured")
    widest = max(tokens.items(), key=lambda kv: len(kv[1].get("departments", ())))
    return str(widest[0])


class _Scout:
    """The two served tools, and nothing else, over one client session."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._pages: dict[str, str] = {}

    async def _call(self, tool: str, arguments: dict[str, object]) -> object:
        result = await self._client.call_tool(tool, arguments)
        payload = result.structured_content or result.data
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"]
        return payload

    async def search(self, query: str, k: int = SEARCH_K) -> list[str]:
        """Return the distinct page paths the served search routed us to."""
        payload = await self._call("wiki_search", {"query": query, "k": k})
        require(
            isinstance(payload, dict) and "results" in payload,
            f"wiki_search returned {type(payload).__name__}, not the envelope",
        )
        assert isinstance(payload, dict)
        hits = payload["results"]
        require(
            isinstance(hits, list),
            f"wiki_search results is {type(hits).__name__}, not a list",
        )
        paths: list[str] = []
        for hit in hits:
            path = hit.get("path") if isinstance(hit, dict) else None
            if isinstance(path, str) and path not in paths:
                paths.append(path)
        return paths

    async def read_text(self, path: str) -> str:
        """The page body an agent would have in context after one read."""
        cached = self._pages.get(path)
        if cached is not None:
            return cached
        page = await self._call("wiki_read", {"path": path, "mode": "full"})
        require(
            isinstance(page, dict),
            f"wiki_read({path!r}) returned {type(page).__name__}, not a page",
        )
        assert isinstance(page, dict)
        parts: list[str] = [str(page.get("tldr") or "")]
        sections = page.get("sections")
        if isinstance(sections, dict):
            parts.extend(str(body) for body in sections.values())
        text = "\n".join(parts)
        self._pages[path] = text
        return text


async def _answerable() -> str:
    """Every recorded answer must come back through search then read."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    pairs = load_pairs()
    require(
        len(pairs) == EXPECTED_PAIRS,
        f"{EVAL_XML}: {len(pairs)} pairs, expected {EXPECTED_PAIRS}",
    )
    vietnamese = [pair for pair in pairs if pair.is_vietnamese]
    require(
        len(vietnamese) >= MIN_VIETNAMESE_PAIRS,
        f"{len(vietnamese)} Vietnamese questions, need at least "
        f"{MIN_VIETNAMESE_PAIRS}: a monolingual set cannot see a regression "
        "that silences one language",
    )

    transport = StreamableHttpTransport(
        SCOUT_URL, headers={"Authorization": f"Bearer {_scout_token()}"}
    )
    unanswered: list[str] = []
    read_paths: set[str] = set()
    async with Client(transport) as client:
        scout = _Scout(client)
        for pair in pairs:
            paths = await scout.search(pair.question)
            if not paths:
                unanswered.append(
                    f"Q{pair.index} {pair.question!r}: wiki_search returned no pages"
                )
                continue
            read_paths.update(paths)
            corpus = "\n".join([await scout.read_text(path) for path in paths])
            if pair.answer not in corpus:
                unanswered.append(
                    f"Q{pair.index} {pair.question!r}: expected {pair.answer!r} "
                    f"in the {len(paths)} page(s) the search routed to "
                    f"({', '.join(paths)}), and it was not there"
                )

    require(
        not unanswered,
        f"{len(unanswered)} of {len(pairs)} questions are no longer answerable "
        "through the served tools:\n  " + "\n  ".join(unanswered),
    )
    print(
        f"  {len(pairs)} questions ({len(vietnamese)} Vietnamese), "
        f"{len(read_paths)} distinct pages searched and read at k={SEARCH_K}; "
        "every recorded answer was present in the read text"
    )
    return "MCP EVAL VERIFIED"


def group_answerable() -> str:
    """An agent with only wiki_search and wiki_read answers all ten."""
    return asyncio.run(_answerable())


GROUPS: dict[str, Callable[[], str]] = {
    "answerable": group_answerable,
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
