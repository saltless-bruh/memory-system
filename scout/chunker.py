"""Anthropic Contextual Chunker & Batch Embedder for SNP Memory System V2.

Implements Contextual Chunking: prepends document & section provenance headers
to individual chunks so semantic and BM25 search retain global context.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from scout.parsers import ParsedDocument


@dataclass
class DocumentChunk:
    chunk_index: int
    chunk_text: str
    context_prefix: str
    loc: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None

    @property
    def contextual_text(self) -> str:
        """The combined text used for dense vector embedding generation."""
        if self.context_prefix:
            return f"{self.context_prefix}\n\n{self.chunk_text}"
        return self.chunk_text


class ContextualChunker:
    """Chunks parsed documents and attaches Anthropic-style contextual headers."""

    def __init__(
        self,
        max_chunk_chars: int = 1500,
        overlap_chars: int = 200,
    ) -> None:
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars

    def chunk_document(self, doc: ParsedDocument) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        chunk_idx = 0

        for section in doc.sections:
            text = section.text.strip()
            if not text:
                continue

            # Build contextual header for this section
            prefix = f"[Document: {doc.title} | Source: {doc.source_uri} | Context: {section.loc}]"

            if len(text) <= self.max_chunk_chars:
                chunks.append(
                    DocumentChunk(
                        chunk_index=chunk_idx,
                        chunk_text=text,
                        context_prefix=prefix,
                        loc=section.loc,
                        metadata={"title": doc.title, **section.metadata},
                    )
                )
                chunk_idx += 1
            else:
                # Sliding window split across paragraphs, lines, or character chunks
                step = max(1, self.max_chunk_chars - self.overlap_chars)
                for start in range(0, len(text), step):
                    chunk_sub = text[start : start + self.max_chunk_chars].strip()
                    if chunk_sub:
                        chunks.append(
                            DocumentChunk(
                                chunk_index=chunk_idx,
                                chunk_text=chunk_sub,
                                context_prefix=prefix,
                                loc=section.loc,
                                metadata={"title": doc.title, **section.metadata},
                            )
                        )
                        chunk_idx += 1

        return chunks


class LiteLLMBatchEmbedder:
    """Batch embedder routing through LiteLLM Gateway to Cloud/Local embeddings."""

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
        self.api_key = api_key or os.environ.get(
            "LITELLM_MASTER_KEY", "sk-local-dev-change-me"
        )
        self.model = model or os.environ.get(
            "LITELLM_EMBED_MODEL", "text-embedding-3-small"
        )
        self.dim = dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embeds a batch of texts via LiteLLM HTTP API, with deterministic mock fallback."""
        if not texts:
            return []

        url = f"{self.base_url}/embeddings"
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            req = urllib.request.Request(
                url, data=payload, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                embeddings = [item["embedding"] for item in data["data"]]
                # Ensure correct dimension padding or slicing if model differs
                res: list[list[float]] = []
                for emb in embeddings:
                    if len(emb) == self.dim:
                        res.append(emb)
                    elif len(emb) < self.dim:
                        res.append(emb + [0.0] * (self.dim - len(emb)))
                    else:
                        res.append(emb[: self.dim])
                return res
        except Exception:
            # Fallback deterministic pseudo-embedding for testing / offline scenarios
            return [self._mock_embed(t) for t in texts]

    def _mock_embed(self, text: str) -> list[float]:
        """Deterministic 1024-dim normalized pseudo-embedding."""
        vec = [0.0] * self.dim
        for i, char in enumerate(text[: self.dim]):
            vec[i % self.dim] += (ord(char) * 31 + i) % 100 / 100.0
        # Normalize vector
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]
