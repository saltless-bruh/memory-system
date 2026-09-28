"""Unit tests for scout.parsers (multi-format document and image parsing)."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from scout.parsers import (
    VLM_STATUS_OK,
    VLM_STATUS_UNAVAILABLE,
    VLM_STATUS_UNCONFIGURED,
    ParserError,
    extract_image_via_vlm,
    parse_code,
    parse_csv,
    parse_file,
    parse_image,
    parse_markdown,
)


def test_parse_markdown_extracts_frontmatter_and_sections() -> None:
    content = (
        "---\n"
        "title: Test Title\n"
        "summary: One line summary.\n"
        "---\n"
        "# Main Heading\n"
        "Intro text.\n\n"
        "## Sub Heading\n"
        "Sub text details.\n"
    )
    doc = parse_markdown(content, "raw/docs/test.md")
    assert doc.title == "Test Title"
    assert doc.metadata.get("summary") == "One line summary."
    assert len(doc.sections) == 2
    assert doc.sections[0].loc == "Section Main Heading"
    assert "Intro text." in doc.sections[0].text
    assert doc.sections[1].loc == "Section Sub Heading"
    assert "Sub text details." in doc.sections[1].text


def test_parse_csv_chunks_tabular_data() -> None:
    csv_content = "id,name,role\n1,Alice,Admin\n2,Bob,User\n3,Charlie,Auditor\n"
    doc = parse_csv(csv_content, "raw/data/users.csv")
    assert doc.title == "Users"
    assert len(doc.sections) >= 1
    assert "Columns: id, name, role" in doc.sections[0].text
    assert "Row 1: id: 1 | name: Alice | role: Admin" in doc.sections[0].text


def test_parse_markdown_ignores_hash_lines_inside_code_fences() -> None:
    content = (
        "# Runbook\n"
        "Intro.\n\n"
        "```bash\n"
        "# restart the service\n"
        "systemctl restart scout\n"
        "```\n\n"
        "~~~~python\n"
        "# a comment\n"
        "~~~\n"
        "## still inside the four-tilde fence\n"
        "~~~~\n\n"
        "## Rollback\n"
        "Undo it.\n"
    )
    doc = parse_markdown(content, "raw/docs/runbook.md")
    assert [s.loc for s in doc.sections] == ["Section Runbook", "Section Rollback"]
    assert "# restart the service" in doc.sections[0].text
    assert "## still inside the four-tilde fence" in doc.sections[0].text


def test_unclosed_fence_swallows_the_rest_of_the_document() -> None:
    """CommonMark: an unclosed fence runs to the end of the document."""
    doc = parse_markdown("# Top\n```\n# not a heading\n", "raw/docs/x.md")
    assert [s.loc for s in doc.sections] == ["Section Top"]


def test_parse_code_preserves_language_fence() -> None:
    code_content = "def hello():\n    return 'world'\n"
    doc = parse_code(code_content, "raw/code/app.py")
    assert doc.title == "app.py"
    assert len(doc.sections) == 1
    assert doc.sections[0].loc == "Full Source Code"
    assert "```py\ndef hello():" in doc.sections[0].text


def test_parse_image_with_vision_extractor() -> None:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")
        img_path = Path(f.name)

    try:

        def fake_vision_extractor(path: Path, uri: str) -> str:
            return (
                "## Visual Architecture\n"
                "Gateway proxies requests to LiteLLM.\n\n"
                "## Transcribed Text / OCR\n"
                "Latency: 145ms, Throughput: 500 req/s.\n"
            )

        doc = parse_image(
            img_path,
            "raw/images/test_diagram.png",
            vision_extractor=fake_vision_extractor,
        )
        assert doc.title == img_path.stem.replace("-", " ").replace("_", " ").title()
        assert len(doc.sections) == 2
        assert doc.sections[0].loc == "Section Visual Architecture"
        assert "Gateway proxies requests" in doc.sections[0].text
        assert doc.sections[1].loc == "Section Transcribed Text / OCR"
        assert "Latency: 145ms" in doc.sections[1].text
        assert doc.metadata["vlm_status"] == VLM_STATUS_OK
        assert "vlm_error" not in doc.metadata
    finally:
        if img_path.exists():
            img_path.unlink()


def test_parse_image_without_vision_route_indexes_nothing() -> None:
    """No configured vision route means no content — not a synthesised stand-in."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF")
        img_path = Path(f.name)

    try:
        with patch.dict("os.environ", {}, clear=True):
            doc = parse_image(img_path, "raw/images/fallback.jpg")
            assert doc.sections == []
            assert doc.full_text.strip() == ""
            assert "Visual Image Asset" not in repr(doc)
            assert doc.metadata["vlm_status"] == VLM_STATUS_UNCONFIGURED
            assert doc.metadata["type"] == "image"
            assert doc.metadata["format"] == "JPG"
    finally:
        if img_path.exists():
            img_path.unlink()


def test_parse_image_failed_vision_yields_no_invented_prose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed vision extraction must be impossible to mistake for real content.

    Regression guard for the fabricated ``Visual Image Asset: ... Size: N bytes``
    description that used to be embedded, indexed, and served to agents as if it
    were a transcription of the image.
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        img_path = Path(f.name)

    def failing_vision_extractor(path: Path, uri: str) -> str:
        raise ParserError(f"Multimodal vision extraction failed for {uri}: HTTP 404")

    try:
        with caplog.at_level(logging.WARNING, logger="scout.parsers"):
            doc = parse_image(
                img_path,
                "raw/images/inference_dashboard.png",
                vision_extractor=failing_vision_extractor,
            )

        # Nothing to embed, index, or cite.
        assert doc.sections == []
        assert doc.full_text.strip() == ""
        assert "Visual Image Asset" not in repr(doc)

        # The failure is explicit to any caller, and it is visible.
        assert doc.metadata["vlm_status"] == VLM_STATUS_UNAVAILABLE
        assert doc.metadata["vlm_status"] != VLM_STATUS_OK
        assert "HTTP 404" in doc.metadata["vlm_error"]
        assert "vlm_status=unavailable" in caplog.text
        assert "raw/images/inference_dashboard.png" in caplog.text
    finally:
        if img_path.exists():
            img_path.unlink()


def test_parse_image_empty_vision_response_is_marked_unavailable() -> None:
    """A vision model that answers with whitespace has still extracted nothing."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        img_path = Path(f.name)

    try:
        doc = parse_image(
            img_path,
            "raw/images/blank.png",
            vision_extractor=lambda _path, _uri: "   \n  ",
        )
        assert doc.sections == []
        assert doc.metadata["vlm_status"] == VLM_STATUS_UNAVAILABLE
    finally:
        if img_path.exists():
            img_path.unlink()


def test_parse_file_image_vlm_failure_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ingestion entrypoint degrades one image without aborting the batch."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        img_path = Path(f.name)

    # Mirrors `extract_image_via_vlm`, which the configured route calls with its
    # resolved base URL, key and model.
    def failing_route(path: Path, uri: str, **_route: object) -> str:
        raise ParserError(f"Multimodal vision extraction failed for {uri}: HTTP 404")

    try:
        monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.invalid:4000")
        monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-not-used")
        monkeypatch.setattr("scout.parsers.extract_image_via_vlm", failing_route)

        doc = parse_file(img_path)
        assert doc.sections == []
        assert doc.metadata["vlm_status"] == VLM_STATUS_UNAVAILABLE
        assert "Visual Image Asset" not in repr(doc)
    finally:
        if img_path.exists():
            img_path.unlink()


def test_parse_file_unsupported_format_raises_parser_error() -> None:
    with tempfile.NamedTemporaryFile(suffix=".unknown_format", delete=False) as f:
        f.write(b"binary_data")
        dummy_path = Path(f.name)

    try:
        with pytest.raises(ParserError, match="unsupported source format"):
            parse_file(dummy_path)
    finally:
        if dummy_path.exists():
            dummy_path.unlink()


# ── vision retry (leaf-3.1) ──────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for the object `urlopen` yields."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _described(text: str) -> bytes:
    import json as _json

    return _json.dumps({"choices": [{"message": {"content": text}}]}).encode()


def _refusing_opener(fail_times: int, body: bytes | None = None):
    """An opener that refuses `fail_times` times, exactly as a booting gateway does."""
    import urllib.error

    state = {"calls": 0}

    def opener(_request: object, timeout: float | None = None) -> object:
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise urllib.error.URLError(
                ConnectionRefusedError(111, "Connection refused")
            )
        assert body is not None
        return _FakeResponse(body)

    return opener, state


def test_vision_retry_survives_a_refused_first_attempt(tmp_path: Path) -> None:
    """A gateway still booting must not cost the figure permanently.

    Measured 2026-09-15: all seven figures of the one PDF in the corpus were
    lost to `[Errno 111] Connection refused` inside litellm's boot window,
    because the vision call had no retry while the embed path did.
    """
    image = tmp_path / "fig.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    opener, state = _refusing_opener(2, _described("A taxonomy diagram."))

    text = extract_image_via_vlm(
        image,
        "doc.pdf#p.5",
        base_url="http://gateway.test/v1",
        api_key="k",
        opener=opener,
        sleep=lambda _seconds: None,
    )

    assert text == "A taxonomy diagram."
    assert state["calls"] == 3, "the retry must be observed retrying, not inferred"


def test_vision_retry_is_bounded_and_still_fails(tmp_path: Path) -> None:
    """A permanently refused route fails; it does not retry forever."""
    from scout.gateway_retry import MAX_ATTEMPTS

    image = tmp_path / "fig.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    opener, state = _refusing_opener(fail_times=10**6)

    with pytest.raises(ParserError) as caught:
        extract_image_via_vlm(
            image,
            "doc.pdf#p.5",
            base_url="http://gateway.test/v1",
            api_key="k",
            opener=opener,
            sleep=lambda _seconds: None,
        )

    assert state["calls"] == MAX_ATTEMPTS
    assert "doc.pdf#p.5" in str(caught.value)


# ── extraction state vocabulary (leaf-3.2) ───────────────────────────────────


def _figures(count: int) -> list[Any]:
    """`count` captioned figures, the shape `extract_figures` returns."""
    from scout.pdf_structure import ExtractedFigure

    return [
        ExtractedFigure(
            page=n,
            number=str(n),
            caption=f"Figure {n}. A captioned figure.",
            name=f"im{n}",
            data=b"\x89PNG\r\n\x1a\n" + bytes([n]) * 32,
            digest=f"{n:064x}",
        )
        for n in range(1, count + 1)
    ]


def _figure_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    figures: int,
    describe: Callable[[Path, str], str],
) -> dict[str, Any]:
    """Run the real figure path over `figures` figures and return its metadata."""
    from scout import parsers

    monkeypatch.setattr(
        "scout.pdf_structure.extract_figures", lambda _path: _figures(figures)
    )
    metadata: dict[str, Any] = {}
    parsers._pdf_figure_sections(
        tmp_path / "paper.pdf", "raw/papers/paper.pdf", metadata, describe
    )
    return metadata


def test_extraction_state_separates_total_loss_from_a_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """0 of 7 and 6 of 7 are not the same statement.

    Both were `partial` until 2026-09-21, which is how a document that lost
    every figure it captions read as a document that kept most of them. The
    audit found `raw/papers/computers-12-00091.pdf` recorded
    `{'figures_status': {'partial': 110}}` with `figures_described: 0`.
    """
    from scout.parsers import FIGURES_FAILED, FIGURES_OK, FIGURES_PARTIAL, ParserError

    def never(_path: Path, _uri: str) -> str:
        raise ParserError("vision route down")

    described: list[str] = []

    def all_but_one(_path: Path, uri: str) -> str:
        described.append(uri)
        if len(described) == 1:
            raise ParserError("vision route down")
        return "A described figure."

    lost = _figure_metadata(monkeypatch, tmp_path, figures=7, describe=never)
    partial = _figure_metadata(monkeypatch, tmp_path, figures=7, describe=all_but_one)
    whole = _figure_metadata(
        monkeypatch, tmp_path, figures=7, describe=lambda _p, _u: "A described figure."
    )

    assert lost["figures_status"] == FIGURES_FAILED
    assert lost["figures_described"] == 0
    assert partial["figures_status"] == FIGURES_PARTIAL
    assert partial["figures_described"] == 6
    assert whole["figures_status"] == FIGURES_OK
    assert whole["figures_described"] == 7
    assert lost["figures_status"] != partial["figures_status"], (
        "total loss must be sayable in a word that is not the word for degradation"
    )


def test_extraction_state_keeps_no_evidence_separate_from_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A document that captions no figures is complete, not failed."""
    from scout.parsers import FIGURES_FAILED, FIGURES_INCOMPLETE, FIGURES_NO_EVIDENCE

    empty = _figure_metadata(
        monkeypatch, tmp_path, figures=0, describe=lambda _p, _u: "unused"
    )
    assert empty["figures_status"] == FIGURES_NO_EVIDENCE
    assert empty["figure_count"] == 0
    assert FIGURES_NO_EVIDENCE not in FIGURES_INCOMPLETE
    assert FIGURES_FAILED in FIGURES_INCOMPLETE
