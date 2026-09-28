"""The shipped `snp-verify-page` script checks the contract AGENTS.md states.

It ran no test at all, and it disagreed with both AGENTS.md and the vault
linter: it failed any page with an H1 (AGENTS.md requires one), required
`## TL;DR` (AGENTS.md: "Recommended, not mandatory"), checked headings for
presence only, read `[[Page#Section]]` as a link to a page called
`Page#Section`, and demanded a lowercase-hyphen filename of existing pages such
as `AFFiNE.md`, which AGENTS.md's rule -- "New pages use lowercase hyphenated
filenames" -- never covered. A page written to AGENTS.md exited 1.

The script is a standalone `uv run --script` file that ships to clients without
this repository, so it cannot import `scout.vault`. It keeps its own copy of the
authored heading rule instead, and the parity tests below fail if the two
copies ever disagree.
"""

from __future__ import annotations

import importlib.util
import itertools
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scout import vault

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "packages/snp-agent/skills/snp-verify-page/scripts/verify_page.py"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    # Importing a file writes its bytecode beside it, and this file lives in the
    # package tree that ships mirrored into `.agent/` and `.claude/`. Suppress
    # the write for this load only, so the test leaves the package as it found it.
    spec = importlib.util.spec_from_file_location("verify_page_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def test_loading_the_script_leaves_no_bytecode_in_the_package(
    script: ModuleType,
) -> None:
    """The package tree is mirrored byte for byte into `.agent/` and `.claude/`,
    and the docs-contract tests read every file in it as text. A `__pycache__`
    left beside the shipped script breaks both, for every later run."""
    assert not (SCRIPT.parent / "__pycache__").exists()


AGENTS_PAGE = """---
title: Demo Page
type: concept
sources: [https://example.com/source]
---

# Demo Page

## TL;DR
Demo pages show the frame. They are short.

## Background
Background explains the subject, citing [[other-page#Details|details]].

## Provenance
Drawn from the declared source.

## Cross-References
[[other-page]]
[[third-page]]
"""


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    for stem in ("other-page", "third-page"):
        (root / f"{stem}.md").write_text(f"# {stem}\n", encoding="utf-8")
    return root


def _write(root: Path, name: str, text: str) -> Path:
    page = root / name
    page.write_text(text, encoding="utf-8")
    return page


def _checks(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["name"]: check for check in receipt["checks"]}


def _failed(receipt: dict[str, Any]) -> set[str]:
    return {check["name"] for check in receipt["checks"] if not check["ok"]}


def test_a_page_written_to_agents_md_passes(script: ModuleType, tmp_path: Path) -> None:
    page = _write(_vault(tmp_path), "demo-page.md", AGENTS_PAGE)

    receipt = script.verify(page, None)

    assert _failed(receipt) == set(), receipt["checks"]
    assert receipt["ok"] is True


def test_the_h1_is_required_not_forbidden(script: ModuleType, tmp_path: Path) -> None:
    page = _write(
        _vault(tmp_path), "demo-page.md", AGENTS_PAGE.replace("# Demo Page\n", "")
    )

    assert "h1-present" in _failed(script.verify(page, None))


def test_an_h1_inside_a_code_fence_is_not_the_title(
    script: ModuleType, tmp_path: Path
) -> None:
    text = AGENTS_PAGE.replace("# Demo Page\n", "```bash\n# a shell comment\n```\n")
    page = _write(_vault(tmp_path), "demo-page.md", text)

    assert "h1-present" in _failed(script.verify(page, None))


def test_tldr_is_recommended_not_required(script: ModuleType, tmp_path: Path) -> None:
    text = AGENTS_PAGE.replace(
        "## TL;DR\nDemo pages show the frame. They are short.\n\n", ""
    )
    page = _write(_vault(tmp_path), "demo-page.md", text)

    assert _failed(script.verify(page, None)) == set()


def test_provenance_is_required_when_sources_are_declared(
    script: ModuleType, tmp_path: Path
) -> None:
    text = AGENTS_PAGE.replace("## Provenance\nDrawn from the declared source.\n\n", "")
    root = _vault(tmp_path)

    assert "headings-follow-the-frame" in _failed(
        script.verify(_write(root, "demo-page.md", text), None)
    )
    unsourced = text.replace("sources: [https://example.com/source]\n", "")
    assert (
        _failed(script.verify(_write(root, "demo-page.md", unsourced), None)) == set()
    )


def test_heading_order_and_duplication_are_checked_not_just_presence(
    script: ModuleType, tmp_path: Path
) -> None:
    """A presence-only check passed both of these pages."""
    root = _vault(tmp_path)
    duplicated = AGENTS_PAGE + "\n## Cross-References\n[[other-page]]\n"
    tldr_last = (
        AGENTS_PAGE.replace(
            "## TL;DR\nDemo pages show the frame. They are short.\n\n", ""
        )
        + "\n## TL;DR\nDemo pages show the frame.\n"
    )

    for text in (duplicated, tldr_last):
        receipt = script.verify(_write(root, "demo-page.md", text), None)
        assert "headings-follow-the-frame" in _failed(receipt)


def test_a_section_anchor_is_not_part_of_the_link_target(
    script: ModuleType, tmp_path: Path
) -> None:
    text = AGENTS_PAGE.replace(
        "[[third-page]]", "[[third-page#Usage]]\n[[#Background]]"
    )
    page = _write(_vault(tmp_path), "demo-page.md", text)

    checks = _checks(script.verify(page, None))

    assert checks["wikilinks-resolve"]["ok"] is True, checks["wikilinks-resolve"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_an_existing_page_keeps_its_name_but_a_new_one_must_be_lowercase(
    script: ModuleType, tmp_path: Path
) -> None:
    """AGENTS.md: *new* pages use lowercase hyphenated filenames."""
    root = _vault(tmp_path)
    existing = _write(root, "AFFiNE.md", AGENTS_PAGE)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "wiki")
    _git(tmp_path, "commit", "-q", "-m", "vault")
    new = _write(root, "NewPage.md", AGENTS_PAGE)

    assert "filename-is-lowercase-hyphen" not in _failed(script.verify(existing, None))
    assert "filename-is-lowercase-hyphen" in _failed(script.verify(new, None))


def test_outside_git_an_unconventional_name_is_degraded_not_passed(
    script: ModuleType, tmp_path: Path
) -> None:
    page = _write(_vault(tmp_path), "AFFiNE.md", AGENTS_PAGE)

    receipt = script.verify(page, None)

    assert any("filename" in note for note in receipt["degraded"])


# ── parity with the vault linter ────────────────────────────────────────────


def test_the_heading_constants_match_the_vault_linter(script: ModuleType) -> None:
    assert script.REQUIRED_HEADINGS == vault.REQUIRED_HEADINGS
    assert script.RECOMMENDED_HEADINGS == vault.RECOMMENDED_HEADINGS
    assert script.SOURCED_HEADINGS == vault.SOURCED_HEADINGS


def test_the_heading_rule_matches_the_vault_linter(script: ModuleType) -> None:
    """Every short heading sequence gets the same verdict from both copies."""
    alphabet = ("TL;DR", "Background", "Provenance", "Cross-References")
    for length in range(5):
        for headings in itertools.product(alphabet, repeat=length):
            for sourced in (False, True):
                expected = vault.headings_are_valid(
                    headings,
                    frame=vault.HeadingFrame.AUTHORED,
                    sources_declared=sourced,
                )
                actual = script.headings_are_valid(
                    headings, None, sources_declared=sourced
                )
                assert actual == expected, (headings, sourced)
