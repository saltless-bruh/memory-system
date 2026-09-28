"""The distributed contract, the linter and AGENTS.md must describe one frame.

This drifted twice. First the instructions called `## TL;DR` "recommended"
while `REQUIRED_HEADINGS` required it. The "fix" then pinned the instructions
to the code -- while AGENTS.md, which CLAUDE.md names as the operating contract
and ADR-0003 makes authoritative, kept saying TL;DR is "Recommended, not
mandatory" and Provenance is "Required when sources are declared". An agent
authoring to AGENTS.md produced pages `verify-vault` rejected, and one
authoring to the package file produced pages AGENTS.md calls incomplete.

Owner ruling: AGENTS.md wins. So these tests pin both the code and the package
file to AGENTS.md, and exercise the linter on a page written to AGENTS.md's own
template -- asserting against the constants alone is how the second drift got
through, since the constants were the thing that was wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scout import vault
from scout.vault import (
    OPTIONAL_HEADINGS,
    RECOMMENDED_HEADINGS,
    REQUIRED_HEADINGS,
    SOURCED_HEADINGS,
    HeadingFrame,
    headings_are_valid,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FRAME_DOC = (
    REPO_ROOT / "packages/snp-agent/instructions/frontmatter_schema.instructions.md"
)
AGENTS = REPO_ROOT / "AGENTS.md"


@pytest.fixture
def frame_text() -> str:
    return FRAME_DOC.read_text(encoding="utf-8")


def _agents_frame() -> str:
    """The body-frame template in AGENTS.md section 4, and the prose after it."""
    text = AGENTS.read_text(encoding="utf-8")
    start = text.index("Use this body frame:")
    return text[start : text.index("## 5.", start)]


def _agents_section(heading: str) -> str:
    """The template text under one `## heading` in AGENTS.md's body frame."""
    frame = _agents_frame()
    start = frame.index(f"## {heading}\n")
    end = frame.find("\n## ", start + 1)
    return frame[start : end if end != -1 else None]


# ── the code says what AGENTS.md says ────────────────────────────────────────


def test_agents_md_still_states_the_rules_the_code_implements() -> None:
    """If AGENTS.md changes, this fails and the constants must follow it."""
    assert "Recommended, not mandatory" in _agents_section("TL;DR")
    assert "Required when sources are declared" in _agents_section("Provenance")
    assert "`## Cross-References` are required" in _agents_frame()


def test_the_constants_follow_agents_md() -> None:
    assert "TL;DR" not in REQUIRED_HEADINGS, "AGENTS.md: TL;DR is not mandatory"
    assert "TL;DR" in RECOMMENDED_HEADINGS
    assert "Cross-References" in REQUIRED_HEADINGS
    assert "Provenance" in SOURCED_HEADINGS, (
        "Provenance is conditional on sources, not unconditionally optional"
    )


def _agents_template_headings() -> tuple[str, ...]:
    template = _agents_frame().split("```markdown", 1)[1].split("```", 1)[0]
    return tuple(re.findall(r"^## (.+)$", template, flags=re.MULTILINE))


def test_the_agents_md_template_is_a_valid_authored_page() -> None:
    headings = _agents_template_headings()
    assert "Provenance" in headings
    assert headings_are_valid(
        headings, frame=HeadingFrame.AUTHORED, sources_declared=True
    )


def test_tldr_may_be_omitted_from_an_authored_page() -> None:
    assert headings_are_valid(
        ("Background", "Cross-References"), frame=HeadingFrame.AUTHORED
    )


def test_tldr_when_present_is_still_once_and_first() -> None:
    assert not headings_are_valid(
        ("TL;DR", "TL;DR", "Cross-References"), frame=HeadingFrame.AUTHORED
    )
    assert not headings_are_valid(
        ("Cross-References", "TL;DR"), frame=HeadingFrame.AUTHORED
    )


def test_provenance_is_required_when_sources_are_declared() -> None:
    without = ("TL;DR", "Background", "Cross-References")
    assert headings_are_valid(without, frame=HeadingFrame.AUTHORED)
    assert not headings_are_valid(
        without, frame=HeadingFrame.AUTHORED, sources_declared=True
    )
    assert not headings_are_valid(
        ("Cross-References", "Provenance"),
        frame=HeadingFrame.AUTHORED,
        sources_declared=True,
    ), "Provenance sits before Cross-References in the AGENTS.md frame"


def test_cross_references_is_still_required() -> None:
    assert not headings_are_valid(("TL;DR", "Background"), frame=HeadingFrame.AUTHORED)


def test_lint_page_applies_the_sourced_rule_from_frontmatter(tmp_path: Path) -> None:
    """End to end: the linter reads `sources:` and decides Provenance itself."""

    def page(body: str, sources: list[str]) -> vault.Page:
        return vault.Page(
            path=tmp_path / "wiki" / "demo.md",
            frontmatter={"title": "Demo", "type": "concept", "sources": sources},
            body=body,
        )

    def heading_errors(p: vault.Page) -> list[str]:
        result = vault.lint_page(p, frame=HeadingFrame.AUTHORED, schema={})
        return [e for e in result.errors if "section headings" in e]

    no_tldr = "# Demo\n\n## Background\n\nText.\n\n## Cross-References\n\n[[a]]\n"
    assert heading_errors(page(no_tldr, [])) == []
    assert heading_errors(page(no_tldr, ["https://example.com/a"])) != []
    with_provenance = no_tldr.replace(
        "## Cross-References", "## Provenance\n\nFrom a.\n\n## Cross-References"
    )
    assert heading_errors(page(with_provenance, ["https://example.com/a"])) == []


def test_the_compiled_frame_is_unchanged() -> None:
    """`compile_note.py` still emits TL;DR, and its exact frame still catches drift."""
    assert headings_are_valid(("TL;DR", "Cross-References"))
    assert not headings_are_valid(("Cross-References",))


# ── the package file says what AGENTS.md says ────────────────────────────────


def _body_frame(frame_text: str) -> str:
    return frame_text[frame_text.index("## Body frame") :]


def test_every_required_heading_is_called_required(frame_text: str) -> None:
    body = _body_frame(frame_text)
    for heading in REQUIRED_HEADINGS:
        assert f"`## {heading}`" in body, f"{heading} is not named in the body frame"


def test_tldr_is_taught_as_recommended(frame_text: str) -> None:
    body = _body_frame(frame_text)
    for heading in RECOMMENDED_HEADINGS:
        assert f"`## {heading}`" in body
    assert "recommended, not mandatory" in body.lower()


def test_provenance_is_taught_as_required_when_sources_are_declared(
    frame_text: str,
) -> None:
    body = _body_frame(frame_text)
    assert "required when sources are declared" in body.lower()
    assert "optional unconditionally" not in body.lower(), (
        "the package file still teaches the retired, code-side rule"
    )
    for heading in (*SOURCED_HEADINGS, *OPTIONAL_HEADINGS):
        assert f"`## {heading}`" in body, f"{heading} is not named in the frame"


def test_both_heading_frames_are_taught(frame_text: str) -> None:
    """An agent that does not know which frame applies cannot satisfy either.

    A bare substring check against the whole document is not evidence: the
    word "authored" already appears elsewhere in this file (the authored
    catalogue), so that half of a naive check could never fail. Require a
    dedicated subsection and confirm both frame words appear *inside* it, so
    a third frame added later to HeadingFrame is caught here too.
    """
    marker = "### Which frame applies"
    assert marker in frame_text, "the two heading frames are never explained"

    section = frame_text[frame_text.index(marker) :]
    next_h2 = section.find("\n## ", len(marker))
    if next_h2 != -1:
        section = section[:next_h2]

    for frame in HeadingFrame:
        assert frame.value in section.lower(), (
            f"the {frame.value} frame is enforced by lint_page but never "
            "explained, inside the frame-selection subsection, to the agent "
            "authoring against it"
        )
