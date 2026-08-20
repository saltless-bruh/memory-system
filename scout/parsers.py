"""Multi-format document parsers for SNP Memory System V2.

Supports Markdown, PDF, CSV/tabular, Code, Images, and plain text.
Extracts clean text sections along with precise location references (loc).
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import mimetypes
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

#: Values ``parse_image`` writes to ``ParsedDocument.metadata["vlm_status"]``.
#: Anything other than ``VLM_STATUS_OK`` means no vision text was obtained and
#: the document deliberately carries **no sections** — never invented prose.
VLM_STATUS_OK = "ok"
VLM_STATUS_UNAVAILABLE = "unavailable"
VLM_STATUS_UNCONFIGURED = "unconfigured"


class ParserError(RuntimeError):
    """Raised when a source cannot be parsed without fabricating content."""


@dataclass
class ParsedSection:
    loc: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    source_uri: str
    title: str
    sections: list[ParsedSection]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(s.text for s in self.sections if s.text.strip())


def parse_markdown(content: str, source_uri: str) -> ParsedDocument:
    """Parses Markdown content, extracting frontmatter, title, and heading sections."""
    title = Path(source_uri).stem.replace("-", " ").title()
    metadata: dict[str, Any] = {}
    body = content

    # Extract YAML frontmatter if present
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                metadata = yaml.safe_load(parts[1]) or {}
                if "title" in metadata:
                    title = str(metadata["title"])
                body = parts[2]
            except Exception:
                body = content

    # Split body into sections by markdown headers (## or #)
    lines = body.splitlines()
    sections: list[ParsedSection] = []
    current_heading = "Intro"
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("#"):
            if current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    sections.append(
                        ParsedSection(
                            loc=f"Section {current_heading}",
                            text=text,
                            metadata={"heading": current_heading},
                        )
                    )
                current_lines = []
            heading_text = line.lstrip("#").strip()
            current_heading = heading_text if heading_text else "Section"
            current_lines.append(line)
        else:
            current_lines.append(line)

    if current_lines:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append(
                ParsedSection(
                    loc=f"Section {current_heading}",
                    text=text,
                    metadata={"heading": current_heading},
                )
            )

    if not sections:
        sections.append(ParsedSection(loc="Full Document", text=body.strip()))

    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        sections=sections,
        metadata=metadata,
    )


def parse_pdf(
    file_path: Path,
    source_uri: str,
    *,
    vision_extractor: Callable[[Path, str], str] | None = None,
) -> ParsedDocument:
    """Parse a PDF into prose pages plus its tables and figures.

    The text layer alone loses two kinds of evidence. A table flattens into a
    paragraph whose columns interleave, destroying the pairing that *is* its
    meaning; figures are image streams and do not appear at all. Both are
    recovered here through `scout.pdf_structure`, and they are recovered
    differently on purpose: tables are rebuilt from glyph coordinates so no cell
    is ever generated, while figures are described by the vision model because a
    diagram has no verbatim text to preserve.

    Structural extraction never costs the caller the prose: a failure in either
    extractor is recorded in `metadata` and the page text is returned regardless.
    """
    from pypdf import PdfReader

    title = file_path.stem.replace("-", " ").title()
    sections: list[ParsedSection] = []
    metadata: dict[str, Any] = {"type": "pdf"}

    try:
        reader = PdfReader(str(file_path))
        for page_idx, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                sections.append(
                    ParsedSection(
                        loc=f"p.{page_idx}",
                        text=text,
                        metadata={"page": page_idx, "kind": "text"},
                    )
                )
    except Exception as e:
        raise ParserError(f"Could not parse PDF {source_uri}") from e

    if not sections:
        raise ParserError(f"PDF {source_uri} contains no extractable text")

    sections.extend(_pdf_table_sections(file_path, source_uri, metadata))
    sections.extend(
        _pdf_figure_sections(file_path, source_uri, metadata, vision_extractor)
    )

    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        sections=sections,
        metadata=metadata,
    )


def _pdf_table_sections(
    file_path: Path, source_uri: str, metadata: dict[str, Any]
) -> list[ParsedSection]:
    """Reconstructed tables as Markdown, so the grid survives chunking."""
    from scout.pdf_structure import PdfStructureError, extract_tables

    try:
        tables = extract_tables(file_path)
    except PdfStructureError as exc:
        metadata["tables_status"] = "unavailable"
        metadata["tables_error"] = str(exc)
        logger.warning("Table extraction unavailable for %s: %s", source_uri, exc)
        return []

    metadata["tables_status"] = "ok"
    metadata["table_count"] = len(tables)
    return [
        ParsedSection(
            loc=table.loc,
            text=table.to_markdown(),
            metadata={
                "page": table.page,
                "kind": "table",
                "table_number": table.number,
                "rows": len(table.rows),
                "columns": max(len(r) for r in table.rows),
            },
        )
        for table in tables
        if table.rows
    ]


def _pdf_figure_sections(
    file_path: Path,
    source_uri: str,
    metadata: dict[str, Any],
    vision_extractor: Callable[[Path, str], str] | None,
) -> list[ParsedSection]:
    """Vision descriptions of the document's captioned figures.

    A figure the model cannot read contributes **no section** and records why —
    the same contract `parse_image` follows. An invented description of a
    diagram is indistinguishable from a real one to a reader, which is exactly
    what makes fabricating it unacceptable.
    """
    from scout.pdf_structure import PdfStructureError, extract_figures

    try:
        figures = extract_figures(file_path)
    except PdfStructureError as exc:
        metadata["figures_status"] = "unavailable"
        metadata["figures_error"] = str(exc)
        logger.warning("Figure extraction unavailable for %s: %s", source_uri, exc)
        return []

    metadata["figure_count"] = len(figures)
    if not figures:
        metadata["figures_status"] = "ok"
        return []

    have_route = bool(
        os.environ.get("LITELLM_BASE_URL") or os.environ.get("LITELLM_MASTER_KEY")
    )
    if vision_extractor is None and not have_route:
        metadata["figures_status"] = "unconfigured"
        logger.warning(
            "No vision route configured; %d figure(s) in %s are not described.",
            len(figures),
            source_uri,
        )
        return []

    sections: list[ParsedSection] = []
    described = 0
    with tempfile.TemporaryDirectory(prefix="snp-figures-") as tmp:
        for figure in figures:
            suffix = mimetypes.guess_extension(figure.mime) or ".png"
            path = Path(tmp) / f"{figure.digest[:16]}{suffix}"
            path.write_bytes(figure.data)
            uri = f"{source_uri}#{figure.loc}"
            try:
                extractor = vision_extractor or extract_image_via_vlm
                described_text = extractor(path, uri)
            except ParserError as exc:
                logger.warning("No vision text for %s: %s", uri, exc)
                continue
            if not described_text or not described_text.strip():
                continue
            described += 1
            body = f"{figure.caption}\n\n{described_text}" if figure.caption else described_text
            sections.append(
                ParsedSection(
                    loc=figure.loc,
                    text=body,
                    metadata={
                        "page": figure.page,
                        "kind": "figure",
                        "figure_number": figure.number,
                        "image_digest": figure.digest,
                        "vlm_status": "ok",
                    },
                )
            )
    metadata["figures_status"] = "ok" if described == len(figures) else "partial"
    metadata["figures_described"] = described
    return sections


def parse_csv(content: str, source_uri: str) -> ParsedDocument:
    """Parses CSV tabular data into structured row representations."""
    title = Path(source_uri).stem.replace("-", " ").title()
    sections: list[ParsedSection] = []

    try:
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        if rows:
            header = rows[0]
            header_str = ", ".join(header)
            row_texts: list[str] = []
            for row_idx, row in enumerate(rows[1:], start=1):
                row_str = " | ".join(
                    f"{header[i]}: {val}"
                    for i, val in enumerate(row)
                    if i < len(header)
                )
                row_texts.append(f"Row {row_idx}: {row_str}")

            # Chunk into blocks of 20 rows
            chunk_size = 20
            for i in range(0, len(row_texts), chunk_size):
                block = row_texts[i : i + chunk_size]
                loc = f"Rows {i + 1}-{i + len(block)}"
                text = f"Columns: {header_str}\n" + "\n".join(block)
                sections.append(ParsedSection(loc=loc, text=text))
    except (csv.Error, UnicodeError) as exc:
        raise ParserError(f"Could not parse CSV {source_uri}") from exc

    if not sections:
        sections.append(ParsedSection(loc="All Rows", text=content.strip()))

    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        sections=sections,
    )


def parse_code(content: str, source_uri: str) -> ParsedDocument:
    """Parses source code files preserving blocks."""
    title = Path(source_uri).name
    ext = Path(source_uri).suffix.lstrip(".")
    text = f"```{ext}\n{content.strip()}\n```"
    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        sections=[ParsedSection(loc="Full Source Code", text=text)],
    )


def extract_image_via_vlm(
    file_path: Path,
    source_uri: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = 60.0,
) -> str:
    """Calls LiteLLM multimodal vision endpoint (Gemini Vision) with Base64 data URI."""
    base = (
        base_url or os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    ).rstrip("/")
    key = api_key or os.environ.get("LITELLM_MASTER_KEY", "")
    vlm_model = model or os.environ.get("LITELLM_VLM_MODEL", "snp-vlm")

    suffix = file_path.suffix.lower()
    mime = "image/svg+xml" if suffix == ".svg" else f"image/{suffix.lstrip('.')}"
    if suffix == ".jpg":
        mime = "image/jpeg"

    try:
        image_bytes = file_path.read_bytes()
    except OSError as exc:
        raise ParserError(f"Could not read image file {source_uri}") from exc

    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    data_uri = f"data:{mime};base64,{b64_data}"

    prompt = (
        "Analyze this technical image asset for knowledge base indexing. "
        "Extract all details and return clean markdown with clear sections:\n"
        "## Visual Overview\n"
        "Detailed architecture, component hierarchy, connections, and flows.\n"
        "## UI & Telemetry Data\n"
        "Metrics, latency graphs, table rows, and status indicators.\n"
        "## Transcribed Text / OCR\n"
        "All visible headers, labels, and exact code snippets."
    )

    payload = {
        "model": vlm_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "temperature": 0.1,
    }

    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices")
            if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                msg = choices[0].get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
    except Exception as exc:
        raise ParserError(
            f"Multimodal vision extraction failed for {source_uri}: {exc}"
        ) from exc

    raise ParserError(f"No content returned from vision model for {source_uri}")


def parse_image(
    file_path: Path,
    source_uri: str,
    *,
    vision_extractor: Callable[[Path, str], str] | None = None,
) -> ParsedDocument:
    """Parses image assets via Gemini Vision VLM extraction or custom extractor.

    A failed or unconfigured vision extraction never produces prose. The returned
    document then carries **zero sections** and an explicit
    ``metadata["vlm_status"]`` of ``"unavailable"`` / ``"unconfigured"``, so a
    caller can always tell a real transcription from a failure, nothing
    fabricated is ever embedded, indexed, or cited as evidence, and a single
    unreadable image cannot abort a whole ingestion batch.

    Args:
        file_path: Image on disk.
        source_uri: Repo-relative address recorded on the document.
        vision_extractor: Optional injected extractor, used instead of the
            configured LiteLLM vision route.

    Returns:
        A ``ParsedDocument`` with ``metadata["vlm_status"] == VLM_STATUS_OK`` and
        the transcribed sections, or a sectionless document stamped with the
        failure status.
    """
    title = file_path.stem.replace("-", " ").replace("_", " ").title()
    size_bytes = os.path.getsize(file_path) if file_path.exists() else 0
    ext = file_path.suffix.lstrip(".").upper()
    metadata: dict[str, Any] = {
        "type": "image",
        "format": ext,
        "size_bytes": size_bytes,
    }

    extracted_markdown: str | None = None
    status = VLM_STATUS_OK
    failure = ""

    if vision_extractor is not None:
        try:
            extracted_markdown = vision_extractor(file_path, source_uri)
        except ParserError as exc:
            status, failure = VLM_STATUS_UNAVAILABLE, str(exc)
    elif os.environ.get("LITELLM_BASE_URL") or os.environ.get("LITELLM_MASTER_KEY"):
        try:
            extracted_markdown = extract_image_via_vlm(file_path, source_uri)
        except ParserError as exc:
            status, failure = VLM_STATUS_UNAVAILABLE, str(exc)
    else:
        status = VLM_STATUS_UNCONFIGURED
        failure = "no vision route configured (LITELLM_BASE_URL/LITELLM_MASTER_KEY unset)"

    if extracted_markdown and extracted_markdown.strip():
        doc = parse_markdown(extracted_markdown, source_uri)
        metadata["vlm_status"] = VLM_STATUS_OK
        return ParsedDocument(
            source_uri=source_uri,
            title=title,
            sections=doc.sections,
            metadata=metadata,
        )

    if status == VLM_STATUS_OK:
        status = VLM_STATUS_UNAVAILABLE
        failure = "vision extraction returned no content"

    logger.warning(
        "No vision text for image %s (vlm_status=%s): %s. "
        "Indexing zero sections rather than fabricating a description.",
        source_uri,
        status,
        failure,
    )
    metadata["vlm_status"] = status
    metadata["vlm_error"] = failure
    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        sections=[],
        metadata=metadata,
    )


def parse_file(
    file_path: Path,
    base_dir: Path | None = None,
    *,
    vision_extractor: Callable[[Path, str], str] | None = None,
) -> ParsedDocument:
    """Unified entrypoint to parse any supported file format."""
    if not file_path.is_file():
        raise ParserError(f"Source is not a regular file: {file_path}")
    rel_uri = (
        str(file_path.relative_to(base_dir))
        if base_dir and file_path.is_relative_to(base_dir)
        else str(file_path)
    )

    suffix = file_path.suffix.lower()

    if suffix in (".md", ".txt", ".markdown"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return parse_markdown(content, rel_uri)
    elif suffix == ".pdf":
        return parse_pdf(file_path, rel_uri, vision_extractor=vision_extractor)
    elif suffix in (".csv", ".tsv"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return parse_csv(content, rel_uri)
    elif suffix in (".py", ".sh", ".json", ".yaml", ".yml", ".sql", ".js", ".ts"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return parse_code(content, rel_uri)
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"):
        return parse_image(file_path, rel_uri, vision_extractor=vision_extractor)
    raise ParserError(f"unsupported source format: {suffix or '<none>'}")
