"""Tests for vault-wide lint collection and deterministic index rendering."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scout import vault
from scripts import gen_index


def _page(wiki: Path, relative: str, title: str, page_type: str = "concept") -> vault.Page:
    return vault.Page(
        path=wiki / relative,
        frontmatter={
            "type": page_type,
            "title": title,
            "summary": f"{title} has one summary sentence.",
            "entities": [Path(relative).stem],
            "department": "ai_eng",
            "sources": [],
            "last_compiled": "2026-08-18",
        },
        body="""## TL;DR

Summary.

## Technical Specifications

Details.

## Provenance

None.

## Cross-References

[[missing-page]]
""",
    )


def test_render_index_includes_navigation_pages_and_all_non_generated_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    pages = [
        _page(wiki, "archive.md", "Archive"),
        _page(wiki, "log.md", "Change Log"),
        _page(wiki, "concepts/content.md", "Content"),
    ]
    # render_index derives links relative to the configured wiki directory.
    monkeypatch.setattr(gen_index, "WIKI_DIR", wiki)

    rendered = gen_index.render_index(pages)

    assert "_3 pages" in rendered
    assert "[Archive](archive.md)" in rendered
    assert "[Change Log](log.md)" in rendered
    assert "[Content](concepts/content.md)" in rendered


def test_collect_lint_keeps_broken_links_and_orphans_as_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    raw = tmp_path / "raw"
    raw.mkdir()
    for entry in vault.REQUIRED_TREE:
        target = wiki / entry
        if Path(entry).suffix:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("generated", encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
    page = _page(wiki, "concepts/content.md", "Content")
    monkeypatch.setattr(gen_index, "WIKI_DIR", wiki)
    monkeypatch.setattr(vault, "RAW_DIR", raw)

    result = gen_index.collect_lint([page])

    assert result.errors == []
    assert any("resolves to no page" in warning for warning in result.warnings)
    assert any("orphan" in warning for warning in result.warnings)


def test_atomic_index_write_replaces_complete_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "wiki" / "index.md"
    index.parent.mkdir()
    index.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(gen_index, "INDEX_PATH", index)

    gen_index._atomic_write_index("new\n")

    assert index.read_text(encoding="utf-8") == "new\n"
    assert not list(index.parent.glob(f".{index.name}.*.tmp"))


def test_atomic_index_write_failure_preserves_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = tmp_path / "wiki" / "index.md"
    index.parent.mkdir()
    index.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(gen_index, "INDEX_PATH", index)

    def fail_replace(_source: os.PathLike[str], _destination: os.PathLike[str]) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(gen_index.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failure"):
        gen_index._atomic_write_index("new\n")

    assert index.read_text(encoding="utf-8") == "old\n"
    assert not list(index.parent.glob(f".{index.name}.*.tmp"))
