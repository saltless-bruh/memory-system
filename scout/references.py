"""A document's reference list, parsed into structured citations.

Two problems share one solution here, and that is why this module exists.

**Retrieval.** The reference list is 21% of this corpus's text and 18% of its
indexed chunks, and reference entries are dense with exact paper titles — so a
BM25 query that *is* a paper title ranks the bibliography above the section that
discusses it. Measured: for hint "Convolutional Neural Networks", rank 1 was
``ACM 2017, 60, 84-90. [CrossRef] 50. Hinton, G.E. Deep belief networks`` and the
actual CNN section came back second.

**The citation graph (T4.2).** The same 87 entries are the second axis of the
knowledge graph — paper cites work. They are raw material, not prose.

So the reference list stops being retrievable text and becomes document
metadata. One change, two problems.

**What this parser will and will not claim.** A reference is a closed shape, so
it is *parsed*, not prompted — an LLM would introduce variance for no gain. But
splitting authors from titles reliably across publisher styles is not something
a regex does, and a confidently wrong title is worse than an absent one. So:

* ``index``, ``text``, ``year``, ``url``, ``doi`` are extracted and reliable.
* ``title`` is populated **only** when the shape is unambiguous, and is ``None``
  otherwise, counted rather than hidden.

That asymmetry is deliberate. Every entry keeps its verbatim ``text``, so a
citation is never lost — only some are less structured than others.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

#: The heading that opens a reference list. Anchored to a line of its own so a
#: sentence mentioning "references" mid-body cannot start the section.
REFERENCE_HEADING = re.compile(r"^\s*(?:References|Bibliography|Works Cited)\s*$", re.M)

#: A numbered entry begins a line: `12. Author, A.; ...`
ENTRY_START = re.compile(r"^\s*(\d{1,3})\.\s+(?=\S)")

#: A running page footer injected mid-list by the PDF's pagination, e.g.
#: `Computers 2023, 12, 91 24 of 26`. It is not part of any entry.
PAGE_FOOTER = re.compile(r"^.{0,80}?\b\d+\s+of\s+\d+\s*$")

_YEAR = re.compile(r"\b(1[89]\d{2}|20[0-9]\d)\b")
_URL = re.compile(r"https?://[^\s,;)\]]+")
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s,;)\]]+", re.I)

#: An author list ends at the first `. ` that follows an initial or a surname,
#: and the title runs to the next `. `. Only applied when the entry looks like
#: `Surname, I.; Surname, I. Title. Venue` — anything else yields no title.
_AUTHORS_THEN_TITLE = re.compile(
    r"^(?P<authors>(?:[^.;]+?,\s*[A-Z][A-Za-z.\-]*\.?)(?:\s*;\s*[^.;]+?,\s*[A-Z][A-Za-z.\-]*\.?)*)"
    r"\s*\.\s*"
    r"(?P<title>[^.]{10,300}?)\s*\.\s"
)


@dataclass(frozen=True, slots=True)
class Reference:
    """One entry of a reference list.

    `text` is verbatim and always present. Everything else is best effort, and
    `title` is `None` rather than a guess when the shape was ambiguous.
    """

    index: int
    text: str
    year: int | None = None
    title: str | None = None
    url: str | None = None
    doi: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def find_reference_section(full_text: str) -> int | None:
    """Return the offset where the reference list starts, or None.

    The **last** heading wins: a paper may mention "References" in a table of
    contents before reaching the real one.
    """
    matches = list(REFERENCE_HEADING.finditer(full_text))
    if not matches:
        return None
    return matches[-1].end()


def _entry_blocks(section: str) -> list[tuple[int, str]]:
    """Split a reference section into `(index, joined text)` per entry.

    Entries wrap across lines and the PDF injects page footers between them, so
    a line is a continuation unless it opens a new numbered entry.
    """
    blocks: list[tuple[int, list[str]]] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or PAGE_FOOTER.match(line):
            continue
        match = ENTRY_START.match(line)
        if match:
            blocks.append((int(match.group(1)), [line[match.end() :].strip()]))
        elif blocks:
            blocks[-1][1].append(line)
        # A line before the first numbered entry is section preamble; drop it.
    return [(index, " ".join(parts).strip()) for index, parts in blocks]


def _title_of(text: str) -> str | None:
    """The entry's title, or None when the shape does not clearly carry one."""
    match = _AUTHORS_THEN_TITLE.match(text)
    if match is None:
        return None
    title = " ".join(match.group("title").split())
    # A "title" that is really a venue fragment or an initial run is not one.
    if len(title.split()) < 3:
        return None
    return title


def parse_reference(index: int, text: str) -> Reference:
    year_match = _YEAR.search(text)
    url_match = _URL.search(text)
    doi_match = _DOI.search(text)
    return Reference(
        index=index,
        text=text,
        year=int(year_match.group(1)) if year_match else None,
        title=_title_of(text),
        url=url_match.group(0).rstrip(".") if url_match else None,
        doi=doi_match.group(0).rstrip(".") if doi_match else None,
    )


def parse_references(full_text: str) -> list[Reference]:
    """Parse every numbered entry of a document's reference list."""
    start = find_reference_section(full_text)
    if start is None:
        return []
    return [
        parse_reference(index, text)
        for index, text in _entry_blocks(full_text[start:])
        if text
    ]


#: A line that opens a numbered reference entry, used to tell a continuation
#: page of the bibliography from an appendix that happens to follow it.
_ENTRY_LINE = re.compile(r"^\s*\d{1,3}\.\s+\S")


def looks_like_reference_page(text: str) -> bool:
    """True when a page is bibliography continuation rather than new prose.

    A reference list usually runs to the end of a paper, but not always — an
    appendix can follow it, and dropping an appendix because of where it sits
    would lose real evidence. So a page after the heading is only treated as
    bibliography when it actually reads like one.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    entries = sum(1 for line in lines if _ENTRY_LINE.match(line))
    return entries >= max(2, len(lines) // 4)


def split_reference_text(sections: Sequence[Any]) -> tuple[list[int], int | None]:
    """Classify parsed sections into prose and bibliography.

    Returns:
        `(drop_indices, truncate_index)` — sections to remove entirely, and the
        one section (if any) whose text should be cut at the reference heading
        because prose and bibliography share a page.
    """
    drop: list[int] = []
    truncate: int | None = None
    found = False
    for index, section in enumerate(sections):
        text = getattr(section, "text", "")
        if not found:
            match = REFERENCE_HEADING.search(text)
            if match is None:
                continue
            found = True
            head = text[: match.start()].strip()
            if head:
                truncate = index
            else:
                drop.append(index)
            continue
        if looks_like_reference_page(text):
            drop.append(index)
    return drop, truncate


#: An inline citation marker: `[12]`, `[12,13]`, `[14–17]`. Both hyphen and
#: en-dash appear in PDF text layers, so both open a range.
_INLINE_MARKER = re.compile(r"\[(\d{1,3}(?:\s*[,;–—-]\s*\d{1,3})*)\]")
_RANGE = re.compile(r"^(\d{1,3})\s*[–—-]\s*(\d{1,3})$")

#: A range wider than this is almost certainly not a citation — page spans and
#: numeric intervals share the bracket notation.
MAX_CITATION_RANGE = 30


def cited_indices(text: str) -> list[int]:
    """Reference numbers cited inline in `text`, in order, deduplicated.

    `[14–17]` expands to 14, 15, 16, 17. A range wider than
    `MAX_CITATION_RANGE` is ignored: a bracketed numeric interval that large is
    far more likely a measurement than a citation, and inventing 200 edges from
    one bracket would poison the graph rather than build it.
    """
    found: dict[int, None] = {}
    for marker in _INLINE_MARKER.finditer(text):
        for part in re.split(r"[,;]", marker.group(1)):
            part = part.strip()
            span = _RANGE.match(part)
            if span:
                low, high = int(span.group(1)), int(span.group(2))
                if low <= high and high - low <= MAX_CITATION_RANGE:
                    for number in range(low, high + 1):
                        found[number] = None
            elif part.isdigit():
                found[int(part)] = None
    return list(found)


def cited_references(text: str, references: Sequence[Any]) -> list[dict[str, Any]]:
    """Resolve the inline citations in `text` against a reference list.

    An index with no matching entry is **dropped**, not guessed: a citation
    edge pointing at nothing is worse than a missing one, and a bracketed number
    in prose is not always a citation.

    Returns dicts rather than `Reference` objects because the caller reads these
    straight out of document metadata, where they are already dicts.
    """
    by_index: dict[int, dict[str, Any]] = {}
    for entry in references:
        item = entry if isinstance(entry, dict) else entry.to_dict()
        by_index[int(item["index"])] = item
    return [by_index[i] for i in cited_indices(text) if i in by_index]


def format_citation(entry: dict[str, Any]) -> str:
    """One reference as a single readable line.

    Uses the parsed `title` when there is one and the verbatim `text` otherwise,
    so an entry whose shape the parser declined still renders honestly rather
    than being dropped for lacking a field.
    """
    label = entry.get("title") or " ".join(str(entry.get("text", "")).split())
    if len(label) > 160:
        label = label[:157].rstrip() + "…"
    year = entry.get("year")
    suffix = f" ({year})" if year else ""
    locator = entry.get("doi") or entry.get("url")
    where = f" — {locator}" if locator else ""
    return f"[{entry['index']}] {label}{suffix}{where}"
