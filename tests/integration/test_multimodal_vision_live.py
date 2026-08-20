"""Live multimodal vision ingestion via LiteLLM `snp-vlm`.

History worth keeping: the PNG test here used to assert `len(sections) >= 1`
and `len(full_text) > 50`, and it **passed for the wrong reason**. The vision
route was returning 404 (a retired model), and `parse_image` silently replaced
the failure with an invented sentence — "Visual Image Asset: Inference
Dashboard. Format: PNG, Size: 155 bytes …" — roughly 150 characters, which
satisfied both assertions. The test was certifying the fabrication, not the
extraction (audit findings B2 and B3).

So these tests now assert the *honest* contract in both directions: an image
the model can read yields real transcription, and an image it cannot read
yields **nothing at all**, flagged, with no invented prose.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scout.parsers import parse_file

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The exact shape of the fabrication that B3 removed. It must never reappear.
FABRICATION_MARKER = "visual image asset"


def _require_vision_route() -> None:
    if not os.environ.get("LITELLM_MASTER_KEY", "").strip():
        pytest.fail("live vision test requires LITELLM_MASTER_KEY")
    if not os.environ.get("LITELLM_BASE_URL", "").strip():
        pytest.fail("live vision test requires LITELLM_BASE_URL")


def test_live_vision_transcribes_a_readable_image() -> None:
    """A diagram the model can read becomes real, citable text."""
    _require_vision_route()
    svg_path = REPO_ROOT / "raw" / "images" / "agent_memory_architecture.svg"
    if not svg_path.exists():
        pytest.fail("raw/images/agent_memory_architecture.svg is missing")

    doc = parse_file(svg_path, REPO_ROOT)

    assert doc.title == "Agent Memory Architecture"
    assert doc.metadata.get("type") == "image"
    assert doc.metadata.get("vlm_status") == "ok"
    assert len(doc.sections) >= 1
    assert len(doc.full_text) > 50
    assert FABRICATION_MARKER not in doc.full_text.lower()


def test_live_vision_failure_yields_no_text_rather_than_an_invented_description() -> None:
    """An unreadable image must produce zero evidence, not a plausible sentence.

    `raw/images/inference_dashboard.png` is a 155-byte 64x64 placeholder. Gemini
    rejects it with `400 INVALID_ARGUMENT - "Unable to process input image"`;
    there is genuinely no dashboard in the file to read. The correct outcome is
    an empty document carrying the failure reason, so retrieval reports
    `no_source` and the merge gate reports FAIL — never a fabricated citation.
    """
    _require_vision_route()
    png_path = REPO_ROOT / "raw" / "images" / "inference_dashboard.png"
    if not png_path.exists():
        pytest.skip("placeholder PNG has been replaced with real content")

    doc = parse_file(png_path, REPO_ROOT)

    assert doc.metadata.get("type") == "image"
    if doc.metadata.get("vlm_status") == "ok":
        # The asset was replaced with a readable image: assert the good path.
        assert len(doc.sections) >= 1
        assert FABRICATION_MARKER not in doc.full_text.lower()
        return

    assert doc.sections == [], "an unreadable image must contribute no passages"
    assert doc.full_text.strip() == ""
    assert doc.metadata.get("vlm_status") in {"unavailable", "unconfigured"}
    assert str(doc.metadata.get("vlm_error", "")).strip(), "the failure reason must be recorded"
    assert FABRICATION_MARKER not in str(doc.metadata).lower()
