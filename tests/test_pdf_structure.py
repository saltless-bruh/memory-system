"""Tests for structure-preserving PDF extraction (Step 2).

These run against the real paper in `raw/papers/` when it is present, because
the failures this module exists to prevent are all layout-specific: a borderless
table, a caption that is really a cross-reference, a journal logo mistaken for a
figure. Synthetic fixtures reproduce none of them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.pdf_structure import (  # noqa: E402
    ExtractedTable,
    find_column_splits,
    infer_spaced_text,
)

PAPER = REPO_ROOT / "raw" / "papers" / "computers-12-00091.pdf"
needs_paper = pytest.mark.skipif(not PAPER.exists(), reason="reference paper absent")


# ── pure units: no PDF required ──────────────────────────────────────────────


def _char(text: str, x0: float, x1: float, top: float = 10.0) -> dict[str, object]:
    return {"text": text, "x0": x0, "x1": x1, "top": top}


def test_spaces_are_inferred_from_glyph_gaps() -> None:
    """This PDF encodes no space glyphs; without inference every cell runs together."""
    chars = [_char("a", 0, 5), _char("b", 5, 10), _char("c", 20, 25)]
    assert infer_spaced_text(chars) == "ab c"


def test_characters_on_separate_lines_stay_on_separate_lines() -> None:
    chars = [_char("a", 0, 5, top=10.0), _char("b", 0, 5, top=40.0)]
    assert infer_spaced_text(chars) == "a\nb"


def test_empty_input_yields_empty_text() -> None:
    assert infer_spaced_text([]) == ""


def test_columns_split_on_empty_gutters_not_word_starts() -> None:
    """Splitting on gaps between word *starts* cuts headings in half."""
    words = [
        {"x0": 105.0, "x1": 228.0},   # left column heading, internally dense
        {"x0": 110.0, "x1": 200.0},
        {"x0": 312.0, "x1": 495.0},   # right column, after a real gutter
    ]
    splits = find_column_splits(words)
    assert len(splits) == 1
    assert 228.0 < splits[0] < 312.0, "split must land inside the empty gutter"


def test_narrow_word_spacing_is_not_a_column() -> None:
    """Inter-word gaps must not become columns, or prose becomes a fake table."""
    words = [{"x0": 0.0, "x1": 40.0}, {"x0": 43.0, "x1": 80.0}]
    assert find_column_splits(words) == []


def test_markdown_render_preserves_the_grid() -> None:
    table = ExtractedTable(
        page=6, number="2", caption="Table 2. X.",
        rows=(("Advantages", "Disadvantages"), ("a", "b")),
    )
    md = table.to_markdown()
    assert "| Advantages | Disadvantages |" in md
    assert "| a | b |" in md
    assert table.loc == "p.6 (Table 2)"


def test_markdown_escapes_pipes_so_a_cell_cannot_forge_a_column() -> None:
    table = ExtractedTable(page=1, number="1", caption=None, rows=(("a|b", "c"),))
    assert r"a\|b" in table.to_markdown()


# ── against the real paper ───────────────────────────────────────────────────


@needs_paper
def test_all_three_captioned_tables_are_reconstructed() -> None:
    from scout.pdf_structure import extract_tables

    tables = extract_tables(PAPER)
    assert [t.number for t in tables] == ["1", "2", "3"]
    shapes = {t.number: (len(t.rows), max(len(r) for r in t.rows)) for t in tables}
    assert shapes["2"] == (5, 2), "Table 2 is a 2-column comparison with 4 body rows"
    for table in tables:
        assert table.caption and table.caption.lower().startswith("table")


@needs_paper
def test_table_two_keeps_its_column_pairing() -> None:
    """The advantage/disadvantage pairing IS the table's meaning."""
    from scout.pdf_structure import extract_tables

    table = next(t for t in extract_tables(PAPER) if t.number == "2")
    header = table.rows[0]
    assert "Advantages" in header[0] and "Disadvantages" in header[1]
    # a body row must not smear one column's prose into the other
    body = table.rows[1]
    assert "Disadvantages" not in body[0]


@needs_paper
def test_only_captioned_images_count_as_figures() -> None:
    """14 embedded images reduce to 7 figures: no branding, no duplicates."""
    from scout.pdf_structure import extract_figures

    figures = extract_figures(PAPER)
    assert [f.number for f in figures] == ["1", "2", "3", "4", "5", "6", "7"]
    assert all(f.page != 1 for f in figures), "page 1 holds journal branding only"
    assert len({f.digest for f in figures}) == len(figures), "duplicates must be dropped"


@needs_paper
def test_each_figure_carries_its_own_caption() -> None:
    """Two figures share page 15; each must get its own caption, not the first."""
    from scout.pdf_structure import extract_figures

    by_number = {f.number: f for f in extract_figures(PAPER)}
    assert by_number["6"].caption != by_number["7"].caption
    assert by_number["7"].caption and by_number["7"].caption.startswith("Figure 7")


@needs_paper
def test_extracted_figures_are_decodable_images_at_full_resolution() -> None:
    """Extraction must not corrupt or downscale the source image."""
    import io

    from PIL import Image

    from scout.pdf_structure import extract_figures

    for figure in extract_figures(PAPER):
        image = Image.open(io.BytesIO(figure.data))
        image.load()
        assert image.width > 300 and image.height > 300, f"{figure.loc} looks downscaled"


@needs_paper
def test_parse_pdf_emits_tables_without_a_vision_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tables are deterministic, so they must not depend on model availability."""
    from scout.parsers import parse_file

    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    doc = parse_file(PAPER, REPO_ROOT / "raw")

    kinds = [s.metadata.get("kind") for s in doc.sections]
    assert kinds.count("table") == 3
    assert kinds.count("figure") == 0, "figures cannot be described without a route"
    assert doc.metadata["figures_status"] == "unconfigured"
    assert doc.metadata["figure_count"] == 7, "figures are still counted, just not described"


@needs_paper
def test_a_failing_vision_route_yields_no_figure_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable figure contributes nothing; it never invents a description."""
    from scout.parsers import ParserError, parse_pdf

    monkeypatch.setenv("LITELLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "unused")

    def always_fails(path: Path, uri: str) -> str:
        raise ParserError("vision route down")

    doc = parse_pdf(PAPER, "raw/papers/paper.pdf", vision_extractor=always_fails)
    assert [s for s in doc.sections if s.metadata.get("kind") == "figure"] == []
    assert doc.metadata["figures_described"] == 0
    assert all("Visual Image Asset" not in s.text for s in doc.sections)
