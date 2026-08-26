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


def test_the_deployed_image_reports_figure_extraction_as_unavailable() -> None:
    """The contract that is actually true of what ships, asserted as such.

    This slot held a positive VLM test requiring
    `raw/images/agent_memory_architecture.svg`, which is not in the tracked tree,
    **and** a figure-extraction capability the deployed image deliberately does
    not have: `scout/requirements.txt` installs `pypdf` only, so Pillow and
    `pdfplumber` are both absent (T5.1, decided 2026-08-25). A test requiring an
    absent asset and an absent capability cannot pass by construction, which is
    worse than no test — it reads as coverage while proving nothing.

    So it asserts the deployment contract instead: with Pillow absent, figure
    extraction reports `unavailable` and produces **no count**, because a count
    of zero from a parser that could not look is not a count of zero. This is
    true today and it fails the day the capability silently returns — which is
    the only thing worth catching here.

    The positive test and a committed asset belong in a vision-enabled profile,
    to be built if and when that feature is approved.
    """
    from scout.pdf_structure import PdfStructureError, extract_figures

    pdf = REPO_ROOT / "raw" / "papers" / "computers-12-00091.pdf"
    if not pdf.is_file():
        pytest.skip("the sample corpus document is not present")

    try:
        import PIL  # noqa: F401
    except ImportError:
        # The deployed condition. Extraction must refuse, not return an empty list.
        with pytest.raises(PdfStructureError, match="Pillow"):
            extract_figures(pdf)
        return

    # A host with Pillow installed is *not* what ships. Assert only that the
    # capability is real there, so this test never silently becomes vacuous.
    assert extract_figures(pdf), (
        "Pillow is installed here, so extraction must actually find the "
        "document's figures; an empty result would mean the capability is "
        "broken rather than absent"
    )


def test_live_vision_failure_yields_no_text_rather_than_an_invented_description() -> (
    None
):
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
    assert str(doc.metadata.get("vlm_error", "")).strip(), (
        "the failure reason must be recorded"
    )
    assert FABRICATION_MARKER not in str(doc.metadata).lower()
