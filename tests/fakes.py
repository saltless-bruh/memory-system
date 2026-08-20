"""Deterministic offline test fakes for unit test suites.

These fakes are quarantined for socket-disabled offline testing and MUST NEVER
be used in production or benchmark evaluation suites.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(slots=True)
class DeterministicFakeEmbedder:
    """Deterministic in-memory embedder for offline unit tests."""

    dim: int = 1024
    dims: int = 1024

    def __post_init__(self) -> None:
        if self.dims != 1024 and self.dim == 1024:
            self.dim = self.dims
        elif self.dim != 1024 and self.dims == 1024:
            self.dims = self.dim

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_single(t) for t in texts]

    async def aembed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_texts(texts)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_single(t) for t in texts]

    def _embed_single(self, text: str) -> list[float]:
        width = self.dim or self.dims or 1024
        vec = [0.0] * width
        tokens = text.lower().split()
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
            idx = int(digest, 16) % width
            vec[idx] += 1.0
        # If width is large (>= 128), L2 normalize for cosine / pgvector distance tests
        if width >= 128:
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            return [x / norm for x in vec]
        return vec


# Alias for backward compatibility across test files
FakeEmbedder = DeterministicFakeEmbedder
