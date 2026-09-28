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
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout.gateway_retry import urlopen_with_retry

logger = logging.getLogger(__name__)

#: Values ``parse_image`` writes to ``ParsedDocument.metadata["vlm_status"]``.
#: Anything other than ``VLM_STATUS_OK`` means no vision text was obtained and
#: the document deliberately carries **no sections** — never invented prose.
VLM_STATUS_OK = "ok"
VLM_STATUS_UNAVAILABLE = "unavailable"
VLM_STATUS_UNCONFIGURED = "unconfigured"

#: Values ``_pdf_figure_sections`` writes to ``metadata["figures_status"]``.
#:
#: ``FIGURES_FAILED`` exists because 0-of-7 and 6-of-7 were both ``partial``
#: until 2026-09-21, and they are not the same statement. A partial document
#: carries figure evidence and is missing some of it; a failed one carries none,
#: which is the state the whole corpus was in for
#: ``raw/papers/computers-12-00091.pdf`` (figure_count=7, figures_described=0)
#: while reporting a word that reads like degradation rather than loss.
FIGURES_OK = "ok"
FIGURES_PARTIAL = "partial"
FIGURES_FAILED = "failed"
FIGURES_NO_EVIDENCE = "no_evidence"
FIGURES_UNAVAILABLE = "unavailable"
FIGURES_UNCONFIGURED = "unconfigured"

#: Figure states in which the document does NOT carry the figure evidence its
#: own bytes contain. `no_evidence` is absent on purpose: a document that
#: captions no figures is complete, not degraded.
FIGURES_INCOMPLETE = frozenset(
    {FIGURES_PARTIAL, FIGURES_FAILED, FIGURES_UNAVAILABLE, FIGURES_UNCONFIGURED}
)


#: Extractor states that mean "this document carries everything its bytes
#: contain". Everything else produces a SMALLER document, which is logged and
#: not raised, so nothing downstream notices without being told.
COMPLETE_EXTRACTOR_STATES = frozenset({"ok", "no_evidence"})


def extraction_state(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Summarise what one parse actually extracted, for the document record.

    An outcome, never a capability: `capability_fingerprint` answers what the
    environment could do and must keep answering the same thing for two runs of
    the same environment, so the thing that differs between those runs lives
    here instead (`rag_documents.extraction_status`, migration 008).

    A document with no extractor state at all -- Markdown, a CSV -- is complete
    by construction: there is nothing structural to lose.
    """
    states = {
        name: metadata[key]
        for name, key in (("figures", "figures_status"), ("tables", "tables_status"))
        if metadata.get(key) is not None
    }
    record: dict[str, Any] = {"extractors": states}
    for key in ("figure_count", "figures_described"):
        if metadata.get(key) is not None:
            record[key] = metadata[key]
    incomplete = sorted(
        name for name, state in states.items() if state not in COMPLETE_EXTRACTOR_STATES
    )
    record["complete"] = not incomplete
    if incomplete:
        record["incomplete"] = incomplete
    return record


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


#: A CommonMark fence opener: up to three spaces of indent, then a run of at
#: least three backticks or tildes. A backtick fence's info string may not hold
#: a backtick, or ```` ``code`` ```` inline spans would open fences.
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}(?=[^`]*$)|~{3,})")


def fenced_lines(lines: Sequence[str]) -> frozenset[int]:
    """Indexes of the lines that sit inside a fenced code block, fences included.

    Markdown headings are a line-level syntax, and a fenced block suspends it:
    ``# restart the service`` inside a ```` ```bash ```` block is a shell
    comment, not a heading. Every heading splitter in this system has to ask this
    question first, or a runbook's code comments become sections with their own
    locators and context prefixes -- citations that point at no heading the
    author wrote. The ingest parser and `wiki_read`'s section reader both split
    on headings, so both call this rather than each keeping a fence rule.

    The rule is CommonMark's: a fence closes only on a line of the *same*
    character, at least as long as the opener, with nothing after it but
    whitespace; an unclosed fence runs to the end of the document.
    """
    inside: set[int] = set()
    opener: str | None = None
    for index, line in enumerate(lines):
        if opener is None:
            match = _FENCE_OPEN.match(line)
            if match:
                opener = match.group(1)
                inside.add(index)
            continue
        inside.add(index)
        stripped = line.strip()
        if (
            len(line) - len(line.lstrip(" ")) <= 3
            and stripped
            and set(stripped) == {opener[0]}
            and len(stripped) >= len(opener)
        ):
            opener = None
    return frozenset(inside)


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

    fenced = fenced_lines(lines)

    for index, line in enumerate(lines):
        if line.startswith("#") and index not in fenced:
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
    env: Mapping[str, str] | None = None,
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
    `env` decides the vision route for the figures; see `vision_route`.
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

    # The reference list becomes structured metadata rather than retrievable
    # prose. It is 18% of this corpus's chunks and dense with exact paper
    # titles, so a query that *is* a paper title ranked the bibliography above
    # the section discussing it — and the same entries are the citation graph's
    # raw material. Moving them serves both. See `scout/references.py`.
    sections = _lift_references(sections, metadata)

    sections.extend(_pdf_table_sections(file_path, source_uri, metadata))
    sections.extend(
        _pdf_figure_sections(file_path, source_uri, metadata, vision_extractor, env=env)
    )

    return ParsedDocument(
        source_uri=source_uri,
        title=title,
        sections=sections,
        metadata=metadata,
    )


def _lift_references(
    sections: list[ParsedSection], metadata: dict[str, Any]
) -> list[ParsedSection]:
    """Move the reference list out of the retrievable text and into metadata.

    Records `reference_count` and `references` either way, so a document with no
    bibliography is distinguishable from one whose bibliography was not found —
    the same distinction `figures_status` had to learn.
    """
    from scout.references import (
        REFERENCE_HEADING,
        parse_references,
        split_reference_text,
    )

    full_text = "\n\n".join(s.text for s in sections if s.text.strip())
    references = parse_references(full_text)
    metadata["reference_count"] = len(references)
    metadata["references"] = [r.to_dict() for r in references]
    if not references:
        metadata["references_status"] = "no_evidence"
        return sections

    drop, truncate = split_reference_text(sections)
    metadata["references_status"] = "lifted"
    kept: list[ParsedSection] = []
    for index, section in enumerate(sections):
        if index in drop:
            continue
        if index == truncate:
            # Prose and bibliography share this page; keep the prose half.
            heading = REFERENCE_HEADING.search(section.text)
            head = section.text[: heading.start()].strip() if heading else section.text
            if not head:
                continue
            kept.append(
                ParsedSection(loc=section.loc, text=head, metadata=section.metadata)
            )
            continue
        kept.append(section)
    return kept


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

    # Three states, not two. `ok` means the parser ran and found something;
    # `no_evidence` means it ran fully and found nothing — a fact about the
    # document; `unavailable` means it could not look at all — a fact about the
    # installation. Collapsing the last two is SH-4, and it is what let a
    # document with 7 figures be recorded as a document with none.
    metadata["tables_status"] = "ok" if tables else "no_evidence"
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
    *,
    env: Mapping[str, str] | None = None,
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
        metadata["figures_status"] = FIGURES_UNAVAILABLE
        metadata["figures_error"] = str(exc)
        logger.warning("Figure extraction unavailable for %s: %s", source_uri, exc)
        return []

    metadata["figure_count"] = len(figures)
    if not figures:
        # The parser looked and this document captions no figures. That is a
        # different statement from "could not look", which is `unavailable`.
        metadata["figures_status"] = FIGURES_NO_EVIDENCE
        return []

    extractor = vision_extractor or vision_route(env)
    if extractor is None:
        metadata["figures_status"] = FIGURES_UNCONFIGURED
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
                described_text = extractor(path, uri)
            except ParserError as exc:
                logger.warning("No vision text for %s: %s", uri, exc)
                continue
            if not described_text or not described_text.strip():
                continue
            described += 1
            body = (
                f"{figure.caption}\n\n{described_text}"
                if figure.caption
                else described_text
            )
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
    # Three outcomes, not two. `described == 0` means every figure this
    # document captions was lost -- a failed extraction, not a degraded one --
    # and it has to be sayable in one word or nothing downstream can act on it.
    if described == len(figures):
        metadata["figures_status"] = FIGURES_OK
    elif described == 0:
        metadata["figures_status"] = FIGURES_FAILED
    else:
        metadata["figures_status"] = FIGURES_PARTIAL
    metadata["figures_described"] = described
    return sections


def parse_csv(
    content: str, source_uri: str, *, delimiter: str | None = None
) -> ParsedDocument:
    """Parses CSV or TSV tabular data into structured row representations.

    The delimiter follows the file's own declaration, its suffix, unless the
    caller names one. A ``.tsv`` read with the comma default does not fail: each
    row becomes a single tab-joined cell, the header/value pairing that is the
    table's meaning is gone, and the document still records as ingested.
    """
    title = Path(source_uri).stem.replace("-", " ").title()
    sections: list[ParsedSection] = []
    if delimiter is None:
        delimiter = "\t" if Path(source_uri).suffix.lower() == ".tsv" else ","

    try:
        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
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
    opener: Callable[..., object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Calls LiteLLM multimodal vision endpoint (Gemini Vision) with Base64 data URI."""
    base = (
        base_url or os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    ).rstrip("/")
    key = api_key if api_key is not None else os.environ.get("LITELLM_MASTER_KEY", "")
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

    # Retried, like every other gateway call in this system. Measured
    # 2026-09-15: a daemon restart brought sync-job up alongside litellm, and
    # all seven figures of the one PDF in the corpus failed with
    # `[Errno 111] Connection refused` inside the gateway's boot window. A
    # single attempt turned a few seconds of unavailability into a permanent
    # hole -- `_IndexedSignature.matches()` then reported the document
    # unchanged forever, so nothing ever retried it.
    #
    # `opener` and `sleep` are injectable so a test can drive the real retry
    # loop rather than patch over it.
    try:
        body = urlopen_with_retry(req, timeout=timeout, opener=opener, sleep=sleep)
        data = json.loads(body.decode("utf-8"))
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


def vision_route(
    env: Mapping[str, str] | None = None,
) -> Callable[[Path, str], str] | None:
    """The LiteLLM vision extractor `env` configures, or None when it has none.

    Whether a route exists is a question about *configuration*, and the caller
    decides which configuration that is. It used to be answered from
    `os.environ` here, below every caller. `snpmemory ingest` resolves the
    project `.env` into a mapping and never exports it, so on a host shell
    without LITELLM_* every image and figure was recorded `unconfigured` -- and
    an image that yields no chunks purges the good rows an earlier run had
    published. The same defect was fixed for the connection and the embedder in
    `ingest_directory`; this is the place it was missed.

    ``None`` means this process's own environment, the way `postgres_settings`
    reads it: the sync-job container is configured through its real
    environment. A mapping is authoritative, including about what it lacks --
    the shell does not leak back in behind it, or the answer would again
    depend on how the command happened to be launched.
    """
    source = os.environ if env is None else env
    base_url = source.get("LITELLM_BASE_URL") or ""
    api_key = source.get("LITELLM_MASTER_KEY") or ""
    if not (base_url or api_key):
        return None
    model = source.get("LITELLM_VLM_MODEL") or "snp-vlm"

    def extract(file_path: Path, source_uri: str) -> str:
        # Looked up at call time so a test can replace the network call while
        # the route decision above stays real.
        return extract_image_via_vlm(
            file_path,
            source_uri,
            base_url=base_url or "http://localhost:4000",
            api_key=api_key,
            model=model,
        )

    return extract


def parse_image(
    file_path: Path,
    source_uri: str,
    *,
    vision_extractor: Callable[[Path, str], str] | None = None,
    env: Mapping[str, str] | None = None,
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
        env: The configuration that decides the vision route; see
            `vision_route`. ``None`` means this process's own environment.

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

    extractor = vision_extractor or vision_route(env)
    if extractor is not None:
        try:
            extracted_markdown = extractor(file_path, source_uri)
        except ParserError as exc:
            status, failure = VLM_STATUS_UNAVAILABLE, str(exc)
    else:
        status = VLM_STATUS_UNCONFIGURED
        failure = (
            "no vision route configured (LITELLM_BASE_URL/LITELLM_MASTER_KEY unset)"
        )

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
    env: Mapping[str, str] | None = None,
) -> ParsedDocument:
    """Unified entrypoint to parse any supported file format.

    `env` is the configuration the vision route is read from. A caller that has
    resolved its configuration -- the CLI, from the project `.env` -- must pass
    it; ``None`` means this process's own environment. See `vision_route`.
    """
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
        return parse_pdf(file_path, rel_uri, vision_extractor=vision_extractor, env=env)
    elif suffix in (".csv", ".tsv"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return parse_csv(content, rel_uri)
    elif suffix in (".py", ".sh", ".json", ".yaml", ".yml", ".sql", ".js", ".ts"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return parse_code(content, rel_uri)
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"):
        return parse_image(
            file_path, rel_uri, vision_extractor=vision_extractor, env=env
        )
    raise ParserError(f"unsupported source format: {suffix or '<none>'}")
