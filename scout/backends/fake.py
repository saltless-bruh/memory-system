"""In-memory RAG backend — for tests and as the swappability reference.

`FakeRagBackend` implements `RagBackend` with no external dependency, which is
what lets Scout core be tested fully offline (no RAG-Anything, no network) and
is the concrete demonstration that the engine is a swappable slot (R-4.8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from scout.types import RagChunk, Scope


@dataclass(slots=True)
class FakeRagBackend:
    """A deterministic in-memory `RagBackend`.

    Attributes:
        chunks: The corpus this fake "retrieves" from.
        record_scope: Captures the last `scope` seen, so tests can assert the
            RBAC hook is threaded through even though V1 ignores it (R-4.8).
    """

    chunks: list[RagChunk] = field(default_factory=list)
    record_scope: Scope | None = field(default=None, init=False)

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        """Return seeded chunks ranked by naive hint overlap.

        Path filtering is intentionally NOT done here — enforcing the source
        boundary is Scout's job (post-filter, R-4.3), and leaving it to Scout
        is exactly what the test suite must exercise.
        """
        self.record_scope = scope
        tokens = {t for t in hint.lower().split() if t}

        def overlap(chunk: RagChunk) -> float:
            if not tokens:
                return chunk.score
            words = set(chunk.text.lower().split())
            hits = len(tokens & words)
            return chunk.score + hits

        ranked = sorted(self.chunks, key=overlap, reverse=True)
        return ranked[:k]
