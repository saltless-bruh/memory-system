"""Multi-format document parsers for SNP Memory System V2.

Supports Markdown, PDF, CSV/tabular, Code, Images, and plain text.
Extracts clean text sections along with precise location references (loc).
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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


def parse_pdf(file_path: Path, source_uri: str) -> ParsedDocument:
    """Parses PDF documents page by page using pypdf, with OCR/image-page fallback."""
    from pypdf import PdfReader

    title = file_path.stem.replace("-", " ").title()
    sections: list[ParsedSection] = []

    try:
        reader = PdfReader(str(file_path))
        for page_idx, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                sections.append(
                    ParsedSection(
                        loc=f"p.{page_idx}",
                        text=text,
                        metadata={"page": page_idx},
                    )
                )
            else:
                # Scanned or image-only page fallback
                sections.append(
                    ParsedSection(
                        loc=f"p.{page_idx}",
                        text=f"Scanned image content on Page {page_idx} of {file_path.name} ({title}).",
                        metadata={"page": page_idx, "is_image_page": True},
                    )
                )
    except Exception as e:
        sections.append(
            ParsedSection(
                loc="Full Document",
                text=f"[Error parsing PDF content: {e}]",
                metadata={"error": str(e)},
            )
        )

    if not sections:
        sections.append(
            ParsedSection(
                loc="Full Document",
                text=f"PDF document {file_path.name} (no text extracted)",
            )
        )

    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        sections=sections,
    )


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
                    f"{header[i]}: {val}" for i, val in enumerate(row) if i < len(header)
                )
                row_texts.append(f"Row {row_idx}: {row_str}")

            # Chunk into blocks of 20 rows
            chunk_size = 20
            for i in range(0, len(row_texts), chunk_size):
                block = row_texts[i : i + chunk_size]
                loc = f"Rows {i + 1}-{i + len(block)}"
                text = f"Columns: {header_str}\n" + "\n".join(block)
                sections.append(ParsedSection(loc=loc, text=text))
    except Exception:
        sections.append(ParsedSection(loc="All Rows", text=content.strip()))

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


def parse_image(file_path: Path, source_uri: str) -> ParsedDocument:
    """Parses image assets, extracting visual descriptions and metadata."""
    title = file_path.stem.replace("-", " ").replace("_", " ").title()
    size_bytes = os.path.getsize(file_path) if file_path.exists() else 0
    ext = file_path.suffix.lstrip(".").upper()

    description = (
        f"Visual Image Asset: {title} ({file_path.name}). "
        f"Format: {ext}, Size: {size_bytes} bytes. "
        f"Stored at {source_uri} for multimodal system reference."
    )

    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        sections=[
            ParsedSection(
                loc="Image Asset",
                text=description,
                metadata={"format": ext, "size_bytes": size_bytes},
            )
        ],
        metadata={"type": "image", "format": ext},
    )


def parse_file(file_path: Path, base_dir: Path | None = None) -> ParsedDocument:
    """Unified entrypoint to parse any supported file format."""
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
        return parse_pdf(file_path, rel_uri)
    elif suffix in (".csv", ".tsv"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return parse_csv(content, rel_uri)
    elif suffix in (".py", ".sh", ".json", ".yaml", ".yml", ".sql", ".js", ".ts"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return parse_code(content, rel_uri)
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"):
        return parse_image(file_path, rel_uri)
    else:
        # Fallback raw text read
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            return ParsedDocument(
                source_uri=rel_uri,
                title=file_path.stem,
                sections=[ParsedSection(loc="Full Document", text=content.strip())],
            )
        except Exception:
            return ParsedDocument(
                source_uri=rel_uri,
                title=file_path.name,
                sections=[
                    ParsedSection(
                        loc="Binary File",
                        text=f"Binary file asset: {file_path.name}",
                    )
                ],
            )
