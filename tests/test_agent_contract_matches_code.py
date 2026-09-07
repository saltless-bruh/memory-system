"""The distributed contract must describe the contract the code enforces.

This drifted once already: the instructions called `## TL;DR` "recommended"
while `REQUIRED_HEADINGS` required it, so an agent following the package
authored pages the linter rejected. Asserting against the constants rather
than against prose means the next change to one side fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.vault import OPTIONAL_HEADINGS, REQUIRED_HEADINGS

REPO_ROOT = Path(__file__).resolve().parents[1]
FRAME_DOC = (
    REPO_ROOT / "packages/snp-agent/instructions/frontmatter_schema.instructions.md"
)


@pytest.fixture
def frame_text() -> str:
    return FRAME_DOC.read_text(encoding="utf-8")


def test_every_required_heading_is_called_required(frame_text: str) -> None:
    body = frame_text[frame_text.index("## Body frame") :]
    for heading in REQUIRED_HEADINGS:
        assert f"`## {heading}`" in body, f"{heading} is not named in the body frame"
    assert "recommended" not in body.lower(), (
        "a required heading is described as recommended; TL;DR becomes chunk 0 "
        "of the indexed page and a page without one is not findable by intent"
    )


def test_optional_headings_are_not_described_as_conditional(frame_text: str) -> None:
    body = frame_text[frame_text.index("## Body frame") :]
    assert "required when sources are declared" not in body, (
        "Provenance is unconditionally optional in OPTIONAL_HEADINGS; describing "
        "it as conditional makes an agent add a section it cannot honestly write"
    )
    for heading in OPTIONAL_HEADINGS:
        assert f"`## {heading}`" in body, f"{heading} is not named as optional"


def test_both_heading_frames_are_taught(frame_text: str) -> None:
    """An agent that does not know which frame applies cannot satisfy either.

    A bare substring check against the whole document is not evidence: the
    word "authored" already appears elsewhere in this file (the authored
    catalogue), so that half of a naive check could never fail. Require a
    dedicated subsection and confirm both frame words appear *inside* it, so
    a third frame added later to HeadingFrame is caught here too.
    """
    from scout.vault import HeadingFrame

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
