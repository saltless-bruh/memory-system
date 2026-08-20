"""scout/chunker.py — Contextual text chunker and strict embedding gateway.

Implements structured contextual chunking across ParsedDocument objects,
plus strict LiteLLM embedding calls with dimension and numeric finiteness validation.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from scout.parsers import ParsedDocument, ParsedSection


@runtime_checkable
class Embedder(Protocol):
    """Protocol for embedding providers."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class AsyncEmbedder(Protocol):
    """Protocol for embedding providers used by async request handlers."""

    async def aembed_texts(self, texts: list[str]) -> list[list[float]]: ...


#: A parent locator that addresses table rows, e.g. ``Rows 1-10`` or ``Row 7``.
_ROW_RANGE_LOC = re.compile(r"^Rows?\s+(\d+)(?:\s*[-\u2013\u2014]\s*(\d+))?$", re.IGNORECASE)
#: A row marker as ``parse_csv`` emits it at the start of each row line.
_ROW_MARKER = re.compile(r"^Row\s+(\d+)\s*:", re.MULTILINE)


def _row_span(parent_loc: str, chunk_text: str) -> tuple[int, int] | None:
    """Read back the row range a chunk actually covers from its own markers.

    Args:
        parent_loc: The locator of the section being split.
        chunk_text: One chunk carved out of that section.

    Returns:
        The ``(first, last)`` rows present in this chunk, or ``None`` when the
        parent locator is not row-addressed or the chunk holds no whole row.
    """
    bounds = _ROW_RANGE_LOC.match(parent_loc)
    if bounds is None:
        return None
    low = int(bounds.group(1))
    high = int(bounds.group(2)) if bounds.group(2) else low
    rows = [
        row
        for row in (int(m.group(1)) for m in _ROW_MARKER.finditer(chunk_text))
        if low <= row <= high
    ]
    if not rows:
        return None
    return min(rows), max(rows)


def derive_chunk_loc(
    parent_loc: str | None, chunk_text: str, part: int, total: int
) -> str | None:
    """Derive a locator describing one chunk rather than its whole parent section.

    A split chunk must never inherit the parent locator verbatim: a citation
    would then name rows or a span the quoted text is not from. Row-addressed
    sections get the exact sub-range read off the chunk's own row markers;
    every other locator keeps its parent name and gains an explicit part marker
    (``Full Source Code (2/3)``), which stays meaningful without over-claiming.

    Args:
        parent_loc: Locator of the section this chunk came from.
        chunk_text: The chunk body.
        part: 1-based position of this chunk within the section.
        total: Number of chunks the section was split into.

    Returns:
        The per-chunk locator, or ``parent_loc`` unchanged when the section was
        never split.
    """
    if total <= 1:
        return parent_loc
    if parent_loc is None or not parent_loc.strip():
        return f"Part {part}/{total}"
    parent = parent_loc.strip()
    span = _row_span(parent, chunk_text)
    if span is not None:
        first, last = span
        return f"Row {first}" if first == last else f"Rows {first}-{last}"
    return f"{parent} ({part}/{total})"


@dataclass(slots=True)
class ContextualChunk:
    """One grounded chunk with contextual prefix for semantic retrieval."""

    chunk_index: int
    chunk_text: str
    context_prefix: str
    loc: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None

    @property
    def contextual_text(self) -> str:
        """Combined context prefix and chunk text for dense semantic embedding."""
        if self.context_prefix:
            return f"{self.context_prefix}\n\n{self.chunk_text}"
        return self.chunk_text

    @property
    def text(self) -> str:
        """Alias for chunk_text."""
        return self.chunk_text


class ContextualChunker:
    """Chunks ParsedDocument objects, adding contextual grounding headers."""

    def __init__(
        self,
        max_chunk_chars: int = 1000,
        overlap_chars: int = 100,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        self.max_chunk_chars = (
            chunk_size if chunk_size is not None else max_chunk_chars
        )
        self.overlap_chars = (
            chunk_overlap if chunk_overlap is not None else overlap_chars
        )
        if self.max_chunk_chars <= 0:
            raise ValueError("max_chunk_chars must be positive")
        if self.overlap_chars < 0:
            raise ValueError("overlap_chars must not be negative")

    def chunk_document(self, doc: ParsedDocument) -> list[ContextualChunk]:
        """Chunks all sections of a ParsedDocument into ContextualChunk items."""
        chunks: list[ContextualChunk] = []
        chunk_idx = 0

        for section in doc.sections:
            sec_chunks = self._chunk_section(section, doc, start_idx=chunk_idx)
            chunks.extend(sec_chunks)
            chunk_idx += len(sec_chunks)

        return chunks

    def _context_prefix(self, doc: ParsedDocument, loc: str | None) -> str:
        """Build the contextual retrieval header grounding one chunk."""
        context_parts = [f"Document: {doc.title}"]
        if doc.source_uri:
            context_parts.append(f"Source: {doc.source_uri}")
        if loc:
            context_parts.append(f"Context: {loc}")
        return f"[{' | '.join(context_parts)}]"

    def _split_text(self, text: str) -> list[str]:
        """Split one section into ordered chunk bodies with a sliding overlap."""
        if len(text) <= self.max_chunk_chars:
            return [text]

        bodies: list[str] = []
        overlap = min(self.overlap_chars, self.max_chunk_chars - 1)
        start = 0
        while start < len(text):
            end = min(start + self.max_chunk_chars, len(text))
            if end < len(text):
                boundary = max(
                    text.rfind("\n", start + 1, end),
                    text.rfind(" ", start + 1, end),
                )
                if boundary > start:
                    end = boundary
            chunk_body = text[start:end].strip()
            if chunk_body:
                bodies.append(chunk_body)
            if end >= len(text):
                break
            next_start = end - overlap
            start = next_start if next_start > start else start + 1

        return bodies or [text]

    def _chunk_section(
        self, section: ParsedSection, doc: ParsedDocument, start_idx: int = 0
    ) -> list[ContextualChunk]:
        text = section.text.strip()
        if not text:
            return []

        sec_meta = dict(doc.metadata)
        sec_meta.update(section.metadata)
        sec_meta["title"] = doc.title
        sec_meta["source_uri"] = doc.source_uri

        bodies = self._split_text(text)
        total = len(bodies)
        chunks: list[ContextualChunk] = []
        for offset, body in enumerate(bodies):
            # A split chunk gets its own locator: inheriting the parent verbatim
            # lets a citation name rows the quoted text is not from.
            loc = derive_chunk_loc(section.loc, body, offset + 1, total)
            chunks.append(
                ContextualChunk(
                    chunk_index=start_idx + offset,
                    chunk_text=body,
                    context_prefix=self._context_prefix(doc, loc),
                    loc=loc,
                    metadata=sec_meta,
                )
            )

        return chunks

    def chunk_text(
        self, text: str, file_path: str = "", context_prefix: str = ""
    ) -> list[ContextualChunk]:
        """Convenience method to chunk raw text directly."""
        doc = ParsedDocument(
            title=os.path.basename(file_path) if file_path else "Text",
            source_uri=file_path,
            sections=[ParsedSection(text=text, loc="Full Text")],
        )
        return self.chunk_document(doc)


#: Maximum inputs per embedding request. Gemini's batchEmbedContents rejects
#: anything larger with `400 INVALID_ARGUMENT - "at most 100 requests can be in
#: one batch"`. A 26-page paper produces ~127 chunks, so a single un-split call
#: fails outright: the previous corpus never exposed this because its largest
#: document yielded five chunks.
MAX_EMBED_BATCH = 100

#: Per-request timeout. A full 100-input batch is a real round trip; the former
#: 15s was tuned against handfuls of chunks and times out at batch scale.
EMBED_TIMEOUT_SECONDS = 60.0


class EmbeddingError(RuntimeError):
    """Raised when an embedding request to LiteLLM fails."""


class LiteLLMBatchEmbedder:
    """Production batch embedder routing through LiteLLM Gateway."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dim: int = 1024,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("LITELLM_BASE_URL", "http://localhost:4000/v1")
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("LITELLM_MASTER_KEY")
        self.model = model or os.environ.get("SCOUT_EMBED_MODEL", "snp-embed")
        self.dim = dim

    def _request_parts(self, texts: list[str]) -> tuple[str, bytes, dict[str, str]]:
        """Build one authenticated embedding request without performing I/O."""
        if not self.api_key:
            raise EmbeddingError("LiteLLM API key is not configured")

        if self.base_url.endswith("/embeddings"):
            url = self.base_url
        elif self.base_url.endswith("/v1"):
            url = f"{self.base_url}/embeddings"
        else:
            url = f"{self.base_url}/v1/embeddings"
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        return url, payload, headers

    def _validate_response(
        self, data: object, texts: list[str]
    ) -> list[list[float]]:
        """Validate and order a LiteLLM response identically for sync/async I/O."""
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise EmbeddingError("Malformed embedding API response")
        items = data["data"]
        if len(items) != len(texts):
            raise EmbeddingError(
                f"Batch cardinality mismatch: sent {len(texts)} texts, received {len(items)} vectors"
            )

        indexed: dict[int, list[float]] = {}
        for item in items:
            if not isinstance(item, dict):
                raise EmbeddingError("Malformed embedding API response item")
            index = item.get("index")
            raw_embedding = item.get("embedding")
            if isinstance(index, bool) or not isinstance(index, int):
                raise EmbeddingError("Embedding response index must be an integer")
            if index in indexed:
                raise EmbeddingError("Embedding response contains a duplicate index")
            if not isinstance(raw_embedding, list):
                raise EmbeddingError("Embedding response vector must be an array")
            vector: list[float] = []
            for value in raw_embedding:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EmbeddingError("Embedding vector contains a non-numeric value")
                vector.append(float(value))
            indexed[index] = vector

        expected_indices = set(range(len(texts)))
        if set(indexed) != expected_indices:
            raise EmbeddingError("Embedding response indices do not match request order")
        embeddings = [indexed[index] for index in range(len(texts))]

        for idx, emb in enumerate(embeddings):
            if len(emb) != self.dim:
                raise EmbeddingError(
                    f"Embedding dimension mismatch at index {idx}: expected {self.dim}, got {len(emb)}"
                )
            if not all(math.isfinite(x) for x in emb):
                raise EmbeddingError(
                    f"Embedding at index {idx} contains non-finite values (NaN or Inf)"
                )
        return embeddings

    def _embed_one_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed at most `MAX_EMBED_BATCH` texts in a single request."""
        url, payload, headers = self._request_parts(texts)
        try:
            req = urllib.request.Request(
                url, data=payload, headers=headers, method="POST"
            )
            with urllib.request.urlopen(
                req, timeout=EMBED_TIMEOUT_SECONDS
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise EmbeddingError("LiteLLM embedding call failed") from exc
        return self._validate_response(data, texts)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed any number of texts, splitting to respect the provider batch cap.

        Results are concatenated in request order, so the caller's Nth text keeps
        the Nth vector however many requests it took.
        """
        if not texts:
            return []
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), MAX_EMBED_BATCH):
            embeddings.extend(
                self._embed_one_batch(texts[start : start + MAX_EMBED_BATCH])
            )
        return embeddings

    async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed without blocking an async MCP server's event loop.

        Splits on `MAX_EMBED_BATCH` like the synchronous path and reuses one
        client across the batches of a single call.
        """
        if not texts:
            return []
        embeddings: list[list[float]] = []
        try:
            async with httpx.AsyncClient(timeout=EMBED_TIMEOUT_SECONDS) as client:
                for start in range(0, len(texts), MAX_EMBED_BATCH):
                    batch = texts[start : start + MAX_EMBED_BATCH]
                    url, payload, headers = self._request_parts(batch)
                    response = await client.post(url, content=payload, headers=headers)
                    response.raise_for_status()
                    embeddings.extend(self._validate_response(response.json(), batch))
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError("LiteLLM embedding call failed") from exc
        return embeddings


# Alias for backward compatibility
LiteLLMEmbedder = LiteLLMBatchEmbedder
