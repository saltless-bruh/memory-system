"""Tests for deterministic, model-free article decomposition."""

from __future__ import annotations

import json

import pytest

from scout.parsers import ParsedDocument, ParsedSection
from scripts.plan_articles import (
    PlanArticlesError,
    extract_headings,
    propose_articles,
    render_plan,
    slugify,
)


def _doc(*pages: tuple[str, str]) -> ParsedDocument:
    sections = [ParsedSection(loc=loc, text=text) for loc, text in pages]
    return ParsedDocument(source_uri="raw/x.pdf", title="X", sections=sections)


def test_headings_are_extracted_in_section_order() -> None:
    doc = _doc(
        ("p.1", "1 Introduction\nbody text here"),
        ("p.2", "2 Machine Learning\nmore body\n2.1 Supervised Learning\nbody"),
    )
    assert extract_headings(doc) == [
        ("1", "Introduction", "p.1"),
        ("2", "Machine Learning", "p.2"),
        ("2.1", "Supervised Learning", "p.2"),
    ]


def test_repeated_headings_collapse_to_first_occurrence() -> None:
    """ToCs and running headers re-emit headings; a naive scan double-counts."""
    doc = _doc(
        ("p.1", "3 Applications\nbody"),
        ("p.9", "3 Applications\nbody again"),
        ("p.14", "3 Applications\nand again"),
    )
    headings = extract_headings(doc)
    assert headings == [("3", "Applications", "p.1")]


def test_journal_running_headers_are_not_headings() -> None:
    doc = _doc(
        ("p.6", "Computers 2023, 12, 91 6 of 26\n1 Real Heading\nbody"),
    )
    assert extract_headings(doc) == [("1", "Real Heading", "p.6")]


def test_numeric_section_ordering_is_not_lexicographic() -> None:
    doc = _doc(("p.1", "10 Tenth\n2 Second\n2.10 Deep\n2.2 Shallow"))
    assert [n for n, _t, _l in extract_headings(doc)] == ["2", "2.2", "2.10", "10"]


def test_max_depth_limits_subsection_explosion() -> None:
    doc = _doc(("p.1", "1 Overview\n1.1 Middle Layer\n1.1.1 Deepest Layer"))
    shallow = propose_articles(doc, department="ai_eng", max_depth=1)
    assert [a.section for a in shallow] == ["1"]
    deep = propose_articles(doc, department="ai_eng", max_depth=3)
    assert [a.section for a in deep] == ["1", "1.1", "1.1.1"]


def test_duplicate_slugs_are_dropped_not_collided() -> None:
    doc = _doc(("p.1", "1 Overview\n2 Overview\n3 Distinct Title"))
    articles = propose_articles(doc, department="ai_eng")
    assert [a.slug for a in articles] == ["overview", "distinct-title"]


def test_a_document_with_no_headings_refuses_rather_than_guessing() -> None:
    doc = _doc(("p.1", "just prose with no numbered structure at all"))
    with pytest.raises(PlanArticlesError, match="hand-written plan"):
        propose_articles(doc, department="ai_eng")


@pytest.mark.parametrize("bad", ["nope", "", "ALL"])
def test_invalid_department_is_rejected(bad: str) -> None:
    doc = _doc(("p.1", "1 Introduction\nbody"))
    with pytest.raises(PlanArticlesError, match="department"):
        propose_articles(doc, department=bad)


def test_invalid_category_is_rejected() -> None:
    doc = _doc(("p.1", "1 Introduction\nbody"))
    with pytest.raises(PlanArticlesError, match="category"):
        propose_articles(doc, department="ai_eng", category="essay")


def test_plan_rendering_is_byte_identical_across_runs() -> None:
    """The whole point of P2: the skeleton must not move between runs."""
    doc = _doc(("p.1", "1 Introduction\nbody"), ("p.2", "2 Methods\nbody"))
    first = render_plan("raw/x.pdf", propose_articles(doc, department="ai_eng"))
    second = render_plan("raw/x.pdf", propose_articles(doc, department="ai_eng"))
    assert first == second
    parsed = json.loads(first)
    assert parsed["source"] == "raw/x.pdf"
    assert [a["title"] for a in parsed["articles"]] == ["Introduction", "Methods"]
    assert parsed["articles"][0]["department"] == "ai_eng"


def test_slugify_matches_the_compiler_and_refuses_empty() -> None:
    assert slugify("Deep Learning: Approaches") == "deep-learning-approaches"
    with pytest.raises(PlanArticlesError):
        slugify("!!!")
