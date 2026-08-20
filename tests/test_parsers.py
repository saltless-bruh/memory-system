"""Unit tests for scout.parsers (multi-format document and image parsing)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scout.parsers import (
    VLM_STATUS_OK,
    VLM_STATUS_UNAVAILABLE,
    VLM_STATUS_UNCONFIGURED,
    ParserError,
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
    csv_content = (
        "id,name,role\n"
        "1,Alice,Admin\n"
        "2,Bob,User\n"
        "3,Charlie,Auditor\n"
    )
    doc = parse_csv(csv_content, "raw/data/users.csv")
    assert doc.title == "Users"
    assert len(doc.sections) >= 1
    assert "Columns: id, name, role" in doc.sections[0].text
    assert "Row 1: id: 1 | name: Alice | role: Admin" in doc.sections[0].text


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

        doc = parse_image(img_path, "raw/images/test_diagram.png", vision_extractor=fake_vision_extractor)
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

    def failing_route(path: Path, uri: str) -> str:
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
