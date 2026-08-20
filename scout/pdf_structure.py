"""Structure-preserving extraction of tables and figures from PDF pages.

`parse_pdf` reads a PDF's text layer, which is the right primitive for prose and
the wrong one for everything else. Two kinds of evidence are lost by it:

**Tables** collapse into running prose. A two-column comparison table becomes a
paragraph whose left and right columns interleave line by line, so the pairing
that *is* the table's meaning disappears. A cell quoted from that paragraph is
not evidence of anything.

**Figures** are invisible entirely — they are embedded image streams, not text.

The two are handled by deliberately different mechanisms, because they carry
different risk:

* Tables are reconstructed **geometrically**. Every character in the output is
  lifted from the PDF at a known coordinate; nothing is generated. A model
  transcribing a data table could misread a digit, and a wrong number that looks
  authoritative is the worst failure this store can produce (R-3.3, R-4.5). When
  the geometry does not yield a coherent grid, this module emits **nothing** and
  says why, rather than guessing.

* Figures are described by the **vision model**, because a diagram has no
  verbatim text to preserve — describing it is the only option, and it is
  already how SVG assets are handled.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: A caption like "Table 2." / "Figure 7." — the anchor for both extractors.
TABLE_CAPTION_RE = re.compile(r"Table\s*(\d+)\s*\.", re.IGNORECASE)
FIGURE_CAPTION_RE = re.compile(r"Figure\s*(\d+)\s*\.", re.IGNORECASE)

#: A column gutter must be at least this wide (points) to split a row. Narrower
#: gaps are inter-word spacing, and treating them as columns is how a naive
#: text-strategy extractor turns an ordinary paragraph into a 65-row "table".
MIN_GUTTER_POINTS = 8.0

#: Characters closer than this fraction of the median glyph width are one word.
#: This PDF encodes no space glyphs at all, so spacing must be inferred from
#: geometry or every cell reads like "AdvantagesofDeepLearning".
SPACE_GAP_RATIO = 0.28

#: Rows within this many points of each other are the same visual line.
LINE_TOLERANCE_POINTS = 2.0

#: A grid needs at least this many columns and body rows to be a table at all.
MIN_COLUMNS = 2
MIN_BODY_ROWS = 1


class PdfStructureError(RuntimeError):
    """Raised when structural extraction cannot proceed without guessing."""


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    """One reconstructed table. Every cell is text lifted from the page."""

    page: int
    number: str | None
    caption: str | None
    rows: tuple[tuple[str, ...], ...]

    @property
    def loc(self) -> str:
        label = f"Table {self.number}" if self.number else "Table"
        return f"p.{self.page} ({label})"

    def to_markdown(self) -> str:
        """Render as a Markdown table so the grid survives chunking and storage."""
        if not self.rows:
            return ""
        width = max(len(r) for r in self.rows)
        def row(cells: tuple[str, ...]) -> str:
            padded = list(cells) + [""] * (width - len(cells))
            return "| " + " | ".join(c.replace("\n", " ").replace("|", "\\|").strip()
                                     for c in padded) + " |"
        head, *body = self.rows
        lines = [row(head), "|" + "|".join([" --- "] * width) + "|"]
        lines.extend(row(r) for r in body)
        header = f"{self.caption}\n\n" if self.caption else ""
        return f"{header}{chr(10).join(lines)}"


@dataclass(frozen=True, slots=True)
class ExtractedFigure:
    """One content figure: an embedded image whose page carries a caption."""

    page: int
    number: str | None
    caption: str | None
    name: str
    data: bytes
    digest: str
    mime: str = "image/png"

    @property
    def loc(self) -> str:
        label = f"Figure {self.number}" if self.number else "Figure"
        return f"p.{self.page} ({label})"


# ── shared text reconstruction ───────────────────────────────────────────────


def infer_spaced_text(chars: list[dict[str, Any]]) -> str:
    """Join positioned characters into text, inserting spaces at real gaps.

    Characters are grouped into visual lines by their vertical position, then
    within a line a space is inserted wherever the horizontal gap exceeds a
    fraction of the median glyph width. Without this the output of a PDF that
    encodes no space glyphs is one unreadable run of letters.
    """
    if not chars:
        return ""
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    last_top: float | None = None
    for char in sorted(chars, key=lambda c: (round(float(c["top"]), 1), float(c["x0"]))):
        top = float(char["top"])
        if last_top is None or abs(top - last_top) <= LINE_TOLERANCE_POINTS:
            current.append(char)
        else:
            lines.append(current)
            current = [char]
        last_top = top
    if current:
        lines.append(current)

    rendered: list[str] = []
    for line in lines:
        widths = [float(c["x1"]) - float(c["x0"]) for c in line if c["text"].strip()]
        threshold = statistics.median(widths) * SPACE_GAP_RATIO if widths else 1.0
        out, previous = "", None
        for char in line:
            if previous is not None and float(char["x0"]) - float(previous["x1"]) > threshold:
                out += " "
            out += char["text"]
            previous = char
        stripped = out.strip()
        if stripped:
            rendered.append(stripped)
    return "\n".join(rendered).strip()


def find_column_splits(
    words: list[dict[str, Any]], *, min_gutter: float = MIN_GUTTER_POINTS
) -> list[float]:
    """Return x positions of vertical gutters separating columns.

    Word x-spans are merged into occupied blocks; a gap between blocks wider
    than `min_gutter` is a column boundary. Using *spans* rather than word start
    positions matters: starts cluster densely inside a column, and splitting on
    the widest gap between them cuts through the middle of a heading.
    """
    spans = sorted((float(w["x0"]), float(w["x1"])) for w in words)
    if not spans:
        return []
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [
        (merged[i][1] + merged[i + 1][0]) / 2.0
        for i in range(len(merged) - 1)
        if merged[i + 1][0] - merged[i][1] >= min_gutter
    ]


# ── tables: deterministic geometry, never generated ──────────────────────────


def _caption_for(text: str, pattern: re.Pattern[str]) -> tuple[str | None, str | None]:
    """Return (number, caption line) for the caption in `text`.

    A real caption *begins* with its label; prose that merely mentions the table
    ("...increases, see Table 1.") does not. Leading captions are preferred so a
    cross-reference earlier on the page cannot masquerade as the caption. Falls
    back to any match, because a caption is still better identification than
    none when the layout puts something before it.
    """
    fallback: tuple[str | None, str | None] = (None, None)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = pattern.search(line)
        if not match:
            continue
        if match.start() == 0:
            return match.group(1), line
        if fallback == (None, None):
            fallback = (match.group(1), line)
    return fallback


def _page_tables(page: Any) -> list[ExtractedTable]:
    """Reconstruct the captioned table on one pdfplumber page, if any."""
    text = page.extract_text() or ""
    number, caption = _caption_for(text, TABLE_CAPTION_RE)
    if number is None:
        return []

    # Locate the caption LINE, not merely the first word that matches: prose
    # such as "...increases, see Table 1." mentions the label earlier on the
    # page, and anchoring to it would bound the table from the wrong y and
    # label it with a cross-reference.
    caption_top = 0.0
    for word in sorted(page.extract_words(), key=lambda w: float(w["top"])):
        if not TABLE_CAPTION_RE.search(word.get("text", "")):
            continue
        line_chars = [
            c
            for c in page.chars
            if abs(float(c["top"]) - float(word["top"])) <= LINE_TOLERANCE_POINTS
        ]
        spaced = infer_spaced_text(line_chars).replace("\n", " ").strip()
        match = TABLE_CAPTION_RE.search(spaced)
        if match and match.start() == 0:
            caption_top = float(word["top"])
            caption = spaced
            number = match.group(1)
            break
        if caption_top == 0.0:  # remember the first mention as a fallback anchor
            caption_top = float(word["top"])

    # Academic tables are ruled horizontally: a top rule, a rule under the
    # header, and a bottom rule. Those rules are the row boundaries.
    edges = sorted(
        {round(float(e["top"]), 1) for e in page.edges if e.get("orientation") == "h"}
    )
    rules = [e for e in edges if e > caption_top]
    if len(rules) < 3:
        return []

    top, bottom = rules[0], rules[-1]
    words = [w for w in page.extract_words() if top <= float(w["top"]) <= bottom]
    if not words:
        return []

    splits = find_column_splits(words)
    if len(splits) + 1 < MIN_COLUMNS:
        return []
    bounds = [0.0, *splits, float(page.width)]

    rows: list[tuple[str, ...]] = []
    for upper, lower in zip(rules, rules[1:], strict=False):
        cells: list[str] = []
        for left, right in zip(bounds, bounds[1:], strict=False):
            chars = [
                c
                for c in page.chars
                if upper <= float(c["top"]) < lower and left <= float(c["x0"]) < right
            ]
            cells.append(infer_spaced_text(chars).replace("\n", " ").strip())
        if any(cells):
            rows.append(tuple(cells))

    if len(rows) < MIN_BODY_ROWS + 1:  # a header alone is not a table
        return []
    return [
        ExtractedTable(
            page=page.page_number, number=number, caption=caption, rows=tuple(rows)
        )
    ]


def extract_tables(file_path: Path) -> list[ExtractedTable]:
    """Reconstruct every captioned, ruled table in a PDF.

    Returns an empty list rather than raising when a document simply has no
    tables. Emits nothing for a table whose geometry does not resolve into a
    coherent grid — an unreconstructable table is reported as absent, never
    approximated.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise PdfStructureError("pdfplumber is required for table extraction") from exc

    tables: list[ExtractedTable] = []
    with pdfplumber.open(str(file_path)) as pdf:
        for page in pdf.pages:
            try:
                tables.extend(_page_tables(page))
            except Exception:  # noqa: BLE001 - one bad page must not lose the rest
                continue
    return tables


# ── figures: caption-gated, deduplicated, model-described ────────────────────


def extract_figures(file_path: Path) -> list[ExtractedFigure]:
    """Return the content figures of a PDF, excluding decoration and duplicates.

    Two filters do the work, and both are necessary:

    * **Caption gating.** A figure is an image on a page that captions one.
      Journal front matter carries logos and licence badges — this paper's first
      page holds five such images, four of them byte-identical — and none of them
      is evidence. Describing them would spend a model call per page to store
      "this is a journal logo" as citable content.

    * **Content hashing.** The same figure often appears as several XObject
      references; here two images recur verbatim on a later page. Deduplicating
      on the image bytes keeps the first occurrence and its real caption.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise PdfStructureError("pypdf is required for figure extraction") from exc

    figures: list[ExtractedFigure] = []
    seen: set[str] = set()
    reader = PdfReader(str(file_path))
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        # A page may caption several figures (this paper captions 6 and 7 on one
        # page), so pair each image with its OWN caption line rather than
        # labelling every image on the page with the first caption found.
        captions: list[tuple[str, str]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            match = FIGURE_CAPTION_RE.search(line)
            if match and match.start() == 0:
                captions.append((match.group(1), line))
        if not captions:
            fallback_number, fallback_caption = _caption_for(text, FIGURE_CAPTION_RE)
            if fallback_number is None:
                continue  # no caption here: whatever is on this page is decoration
            captions = [(fallback_number, fallback_caption or "")]
        try:
            images = list(page.images)
        except Exception:  # noqa: BLE001 - a broken stream must not sink the page
            continue
        for index, image in enumerate(images):
            data, mime = _figure_bytes(image)
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            number, caption = (
                captions[index] if index < len(captions) else captions[-1]
            )
            figures.append(
                ExtractedFigure(
                    page=page_number,
                    number=number,
                    caption=caption,
                    name=str(getattr(image, "name", f"image-{index}")),
                    data=data,
                    digest=digest,
                    mime=mime,
                )
            )
    return figures


def _figure_bytes(image: Any) -> tuple[bytes, str]:
    """Return the highest-fidelity bytes available for one embedded image.

    ``pypdf``'s ``ImageFile.data`` decodes the stream and re-serialises it. For a
    ``/DCTDecode`` (JPEG) image that is a *re-encode*: measured against this
    paper, pixels drift by up to 10/255. The PDF already contains a complete,
    valid JPEG file, so it is returned untouched — byte-identical to the source,
    which is what makes an extracted figure inspectable against the original.

    Other filters (``/FlateDecode`` and friends) store raw samples rather than a
    self-contained image file, so there is nothing to pass through; pypdf's
    reconstruction is used and is lossless in its own right, PNG being a
    lossless container.
    """
    reference = getattr(image, "indirect_reference", None)
    if reference is not None:
        try:
            obj = reference.get_object()
            if "/DCTDecode" in str(obj.get("/Filter")):
                raw = bytes(obj._data)
                if raw[:3] == b"\xff\xd8\xff":  # a complete JPEG, not a fragment
                    return raw, "image/jpeg"
        except Exception:  # noqa: BLE001 - fall back to the decoded form
            pass
    return bytes(image.data), _mime_for(str(getattr(image, "name", "")))


def _mime_for(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }.get(suffix, "image/png")
