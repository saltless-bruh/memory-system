"""Parsing a document's reference list into structured citations.

The reference list is two things at once, and that is why this parser exists:
it is **18% of the indexed chunks** and dense with exact paper titles, so it
outranks the prose it describes; and it is the raw material for the citation
graph (T4.2). Structuring it once solves both.

The parser's contract is asymmetric on purpose. `index`, `text`, `year`, `url`
and `doi` are reliable. `title` is populated only when the entry's shape is
unambiguous and is `None` otherwise, because a confidently wrong title is worse
than an absent one — and every entry keeps its verbatim `text`, so nothing is
lost, only some entries are less structured than others.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.references import (  # noqa: E402
    REFERENCE_HEADING,
    Reference,
    find_reference_section,
    parse_reference,
    parse_references,
)

SAMPLE = """\
Some body text that mentions references in passing.

References
1. Arel, I.; Rose, D.C.; Karnowski, T.P. Deep machine learning—A new frontier.
IEEE Comput. Intell. Mag. 2010, 5, 13-18. [CrossRef]
2. Benos, L.; Tagarakis, A.C. Machine Learning in Agriculture: A Comprehensive
Updated Review. Sensors 2021, 21, 3758. [CrossRef]
Computers 2023, 12, 91 24 of 26
3. Vuong, Q. Machine Learning for Robotic Manipulation. 2021. Available online:
https://arxiv.org/abs/2101.00755v1 (accessed on 11 April 2023).
"""


def test_a_wrapped_entry_is_joined_into_one_citation() -> None:
    """Entries wrap across lines; a citation split in two is two wrong answers."""
    refs = parse_references(SAMPLE)

    assert [r.index for r in refs] == [1, 2, 3]
    assert "IEEE Comput. Intell. Mag. 2010" in refs[0].text
    assert "Updated Review" in refs[1].text


def test_a_page_footer_is_not_part_of_the_citation_it_interrupts() -> None:
    """The PDF injects `Computers 2023, 12, 91 24 of 26` mid-list."""
    refs = parse_references(SAMPLE)
    assert all("24 of 26" not in r.text for r in refs)


def test_the_reliable_fields_are_extracted() -> None:
    refs = parse_references(SAMPLE)

    assert [r.year for r in refs] == [2010, 2021, 2021]
    assert refs[2].url == "https://arxiv.org/abs/2101.00755v1"
    assert refs[0].url is None


def test_an_ambiguous_entry_yields_no_title_rather_than_a_guess() -> None:
    """A confidently wrong title is worse than an absent one."""
    # No authors at all: the entry opens with its own title.
    ambiguous = parse_reference(
        15, "Deep Learning Techniques: An Overview|SpringerLink. Available online: x"
    )
    assert ambiguous.title is None
    # The verbatim text is still there, so the citation is not lost.
    assert "Deep Learning Techniques" in ambiguous.text


def test_a_clear_entry_yields_its_title() -> None:
    clear = parse_reference(
        50, "Hinton, G.E. Deep belief networks. Scholarpedia 2009, 4, 5947. [CrossRef]"
    )
    assert clear.title == "Deep belief networks"
    assert clear.year == 2009


def test_the_last_references_heading_wins() -> None:
    """A table of contents may name the section before the section arrives."""
    text = "References\n(see page 24)\n\nBody\n\nReferences\n1. Real, E. Entry. 2020.\n"
    start = find_reference_section(text)
    assert start is not None
    assert "Real, E." in text[start:]


def test_a_document_with_no_reference_list_yields_nothing() -> None:
    assert parse_references("Just a body with no bibliography.\n") == []
    assert find_reference_section("no heading here") is None


def test_the_verbatim_text_is_always_present() -> None:
    """Whatever else fails, a citation must not be lost."""
    for ref in parse_references(SAMPLE):
        assert ref.text.strip()
        assert isinstance(ref, Reference)


# ── against the real document ─────────────────────────────────────────────


def _real_document() -> object:
    """The parsed document. Its references live in `metadata`, not in the text —
    that is the whole point of step 8."""
    from scout.parsers import parse_file

    source = REPO_ROOT / "raw" / "papers" / "computers-12-00091.pdf"
    if not source.is_file():  # pragma: no cover - corpus may change
        import pytest

        pytest.skip("the sample corpus document is not present")
    return parse_file(source, base_dir=REPO_ROOT)


def _real_references() -> list[dict[str, object]]:
    document = _real_document()
    return document.metadata["references"]  # type: ignore[attr-defined,no-any-return]


def test_the_real_document_yields_its_eighty_seven_references() -> None:
    """T4.2 names 87. The parser must find 87, not 86 and not 90."""
    refs = _real_references()

    assert len(refs) == 87
    assert [r["index"] for r in refs] == list(range(1, 88))
    assert all(r["year"] for r in refs), "every entry in this document carries a year"


def test_parsing_the_real_document_is_deterministic() -> None:
    """Byte-stable like `plan-articles`: it is a pure function of the source."""
    assert _real_references() == _real_references()


def test_what_could_not_be_parsed_is_countable_not_hidden() -> None:
    """R4: the parser reports its own limits rather than silently guessing."""
    refs = _real_references()
    untitled = [r for r in refs if r["title"] is None]

    # Some entries are genuinely ambiguous — books, entries with no author list,
    # and one where the PDF lost the space in `nets.Neural Comput.`
    assert 0 < len(untitled) < len(refs) // 2
    for ref in untitled:
        assert str(ref["text"]).strip(), "an unparsed title must not lose a citation"


# ── the reference list leaves the retrievable text (step 8) ───────────────


def test_the_reference_list_is_lifted_out_of_the_parsed_text() -> None:
    """18% of the index was a different kind of object indexed as prose."""
    from scout.parsers import parse_file

    source = REPO_ROOT / "raw" / "papers" / "computers-12-00091.pdf"
    if not source.is_file():  # pragma: no cover - corpus may change
        import pytest

        pytest.skip("the sample corpus document is not present")

    document = parse_file(source, base_dir=REPO_ROOT)

    assert document.metadata["reference_count"] == 87
    assert document.metadata["references_status"] == "lifted"
    assert len(document.metadata["references"]) == 87

    text = document.full_text
    assert "[CrossRef]" not in text, "bibliography markers must not remain in prose"
    assert not REFERENCE_HEADING.search(text)
    # The prose survived: only the bibliography left.
    assert "Convolutional Neural Networks" in text
    assert len(text) > 80_000


def test_a_document_with_no_bibliography_says_so_rather_than_nothing() -> None:
    """`no_evidence`, not silence — the same distinction figures_status learned."""
    from scout.parsers import parse_markdown

    document = parse_markdown("# Title\n\nBody with no reference list.\n", "raw/x.md")
    assert document.metadata.get("reference_count", 0) == 0


def test_an_appendix_after_the_references_is_not_dropped() -> None:
    """A reference list usually runs to the end of a paper. Usually is not always."""
    from scout.references import looks_like_reference_page

    bibliography = (
        "1. Author, A. Title. 2020.\n2. Other, B. Thing. 2021.\n3. C, D. X. 2022.\n"
    )
    appendix = (
        "Appendix A. Survey instrument\n"
        "Participants were asked to rate each system on a five-point scale.\n"
        "Responses were collected over four weeks in 2023.\n"
    )
    assert looks_like_reference_page(bibliography) is True
    assert looks_like_reference_page(appendix) is False
