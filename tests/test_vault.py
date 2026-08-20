"""Regression tests for the mechanical wiki page contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scout import vault

VALID_BODY = """## TL;DR

Dense summary.

## Technical Specifications

Technical detail.

## Provenance

Source provenance.

## Cross-References

[[other-page]]
"""


def _frontmatter(**overrides: Any) -> dict[str, Any]:
    frontmatter: dict[str, Any] = {
        "type": "concept",
        "title": "Test Page",
        "summary": "One complete summary sentence.",
        "entities": ["test-page", "contract"],
        "department": "ai_eng",
        "sources": [
            {
                "path": "raw/source.txt",
                "loc": "lines 1-2",
                "hint": "exact source phrase",
            }
        ],
        "last_compiled": "2026-08-18",
    }
    frontmatter.update(overrides)
    return frontmatter


def _page(tmp_path: Path, **overrides: Any) -> vault.Page:
    return vault.Page(
        path=tmp_path / "wiki" / "concepts" / "test-page.md",
        frontmatter=_frontmatter(**overrides),
        body=VALID_BODY,
    )


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "raw"
    directory.mkdir()
    (directory / "source.txt").write_text("source", encoding="utf-8")
    return directory


def _errors(page: vault.Page, raw_dir: Path) -> list[str]:
    return vault.lint_page(page, raw_dir=raw_dir).errors


@pytest.mark.parametrize("field", vault.REQUIRED_FRONTMATTER)
def test_all_seven_frontmatter_fields_are_required(
    tmp_path: Path, raw_dir: Path, field: str
) -> None:
    frontmatter = _frontmatter()
    del frontmatter[field]
    page = vault.Page(tmp_path / "wiki" / "page.md", frontmatter, VALID_BODY)

    assert any(f"missing frontmatter field '{field}'" in error for error in _errors(page, raw_dir))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", ""),
        ("title", "   "),
        ("summary", ""),
        ("entities", []),
        ("department", ""),
        ("last_compiled", ""),
    ],
)
def test_required_frontmatter_values_must_be_nonempty(
    tmp_path: Path, raw_dir: Path, field: str, value: object
) -> None:
    assert _errors(_page(tmp_path, **{field: value}), raw_dir)


def test_empty_sources_list_is_valid_for_source_free_concepts(
    tmp_path: Path, raw_dir: Path
) -> None:
    page = _page(tmp_path, sources=[])

    assert _errors(page, raw_dir) == []


@pytest.mark.parametrize("page_type", ["Technique", "note", 7, None])
def test_type_must_be_an_exact_canonical_string(
    tmp_path: Path, raw_dir: Path, page_type: object
) -> None:
    assert any("invalid type" in error for error in _errors(_page(tmp_path, type=page_type), raw_dir))


@pytest.mark.parametrize("department", ["all", "AI_ENG", 7, None])
def test_department_must_be_an_exact_canonical_string(
    tmp_path: Path, raw_dir: Path, department: object
) -> None:
    assert any(
        "invalid department" in error
        for error in _errors(_page(tmp_path, department=department), raw_dir)
    )


@pytest.mark.parametrize("entities", ["entity", ["ok", ""], ["ok", 7], [None]])
def test_entities_must_be_a_nonempty_list_of_nonempty_strings(
    tmp_path: Path, raw_dir: Path, entities: object
) -> None:
    assert any("entities" in error for error in _errors(_page(tmp_path, entities=entities), raw_dir))


@pytest.mark.parametrize(
    "summary",
    [
        "No sentence terminator",
        "First sentence. Second sentence.",
        "First line.\nSecond line.",
        42,
    ],
)
def test_summary_is_exactly_one_nonempty_line_and_sentence(
    tmp_path: Path, raw_dir: Path, summary: object
) -> None:
    assert any("summary" in error for error in _errors(_page(tmp_path, summary=summary), raw_dir))


@pytest.mark.parametrize("compiled", ["20260818", "2026-02-30", "18-08-2026", 20260818])
def test_last_compiled_is_an_exact_iso_calendar_date(
    tmp_path: Path, raw_dir: Path, compiled: object
) -> None:
    assert any(
        "last_compiled" in error
        for error in _errors(_page(tmp_path, last_compiled=compiled), raw_dir)
    )


@pytest.mark.parametrize("sources", ["raw/source.txt", {}, None])
def test_sources_must_be_a_list(
    tmp_path: Path, raw_dir: Path, sources: object
) -> None:
    assert any("sources must be a list" in error for error in _errors(_page(tmp_path, sources=sources), raw_dir))


def test_malformed_source_entries_are_not_filtered_before_validation(
    tmp_path: Path, raw_dir: Path
) -> None:
    page = _page(tmp_path, sources=["not-a-mapping", 7])

    errors = _errors(page, raw_dir)
    assert any("sources[0] is not a mapping" in error for error in errors)
    assert any("sources[1] is not a mapping" in error for error in errors)


@pytest.mark.parametrize(
    "source",
    [
        {"path": "", "loc": "lines 1-2", "hint": "phrase"},
        {"path": "raw/source.txt", "loc": "", "hint": "phrase"},
        {"path": "raw/source.txt", "loc": "lines 1-2", "hint": ""},
        {"path": 7, "loc": "lines 1-2", "hint": "phrase"},
    ],
)
def test_source_keys_are_nonempty_strings(
    tmp_path: Path, raw_dir: Path, source: dict[str, object]
) -> None:
    assert any("sources[0]" in error for error in _errors(_page(tmp_path, sources=[source]), raw_dir))


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "source.txt",
        "raw/../outside.txt",
        "raw/nested/../../outside.txt",
    ],
)
def test_source_path_must_resolve_beneath_raw(
    tmp_path: Path, raw_dir: Path, path: str
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    source = {"path": path, "loc": "lines 1-2", "hint": "phrase"}

    assert any("sources[0].path" in error for error in _errors(_page(tmp_path, sources=[source]), raw_dir))


def test_source_symlink_cannot_escape_raw(tmp_path: Path, raw_dir: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (raw_dir / "escape.txt").symlink_to(outside)
    source = {"path": "raw/escape.txt", "loc": "line 1", "hint": "phrase"}

    assert any("escapes raw" in error for error in _errors(_page(tmp_path, sources=[source]), raw_dir))


def test_related_frontmatter_is_forbidden(tmp_path: Path, raw_dir: Path) -> None:
    assert any("forbidden 'related:'" in error for error in _errors(_page(tmp_path, related=[]), raw_dir))


@pytest.mark.parametrize(
    "body",
    [
        VALID_BODY.replace("## Provenance\n", ""),
        VALID_BODY + "\n## Provenance\n\nDuplicate.\n",
        VALID_BODY.replace(
            "## Technical Specifications\n\nTechnical detail.\n\n## Provenance",
            "## Provenance\n\nSource provenance.\n\n## Technical Specifications",
        ),
        VALID_BODY + "\n## Extra Section\n\nNot allowed.\n",
    ],
)
def test_body_has_exactly_one_of_each_required_h2_in_order(
    tmp_path: Path, raw_dir: Path, body: str
) -> None:
    page = _page(tmp_path)
    page.body = body

    assert any("section headings" in error for error in _errors(page, raw_dir))


def test_broken_wikilinks_are_warnings_not_errors(tmp_path: Path, raw_dir: Path) -> None:
    page = _page(tmp_path)

    result = vault.lint_page(page, raw_dir=raw_dir, known_slugs={page.slug})

    assert result.errors == []
    assert any("resolves to no page" in warning for warning in result.warnings)


def test_load_pages_includes_navigation_and_excludes_only_generated_root_index(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    for relative in ("index.md", "archive.md", "log.md", "concepts/index.md", "concepts/page.md"):
        target = wiki / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("body", encoding="utf-8")

    loaded = {page.path.relative_to(wiki).as_posix() for page in vault.load_pages(wiki)}

    assert loaded == {"archive.md", "log.md", "concepts/index.md", "concepts/page.md"}


def test_load_pages_rejects_markdown_symlink_outside_wiki(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    concepts = wiki / "concepts"
    concepts.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (concepts / "escape.md").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        vault.load_pages(wiki)


def test_load_pages_rejects_symlinked_category(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "page.md").write_text("outside", encoding="utf-8")
    (wiki / "concepts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        vault.load_pages(wiki)
