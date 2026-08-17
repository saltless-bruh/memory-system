"""RAG-Anything HTTP backend — the V1 deployment adapter (R-4.8, R-4.2).

RAG-Anything runs in its own container (Python 3.12, because MinerU needs
<3.14) and exposes an internal ``/retrieve`` endpoint reachable ONLY by Scout
on the compose network — never by the agent (R-4.2). This adapter is how Scout
(on the 3.14 host / its own container) reaches it: a plain `RagBackend` that
POSTs ``{hint, k}`` and maps the service's already-parsed chunks into
`RagChunk`s. The fragile LightRAG ``only_need_context`` string parsing lives
server-side in the rag service (`rag/app.py`), where `raw/` is mounted to
resolve file paths, so this adapter just consumes clean JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

from scout.types import RagChunk, Scope


@dataclass(slots=True)
class RagAnythingHttpBackend:
    """`RagBackend` that calls the internal RAG-Anything service over HTTP.

    Attributes:
        base_url: The rag service base URL (``http://rag:8000`` on the compose
            network — internal only, no published port).
        timeout: Per-request timeout in seconds (retrieval can be slow while
            the KG query runs).
    """

    base_url: str = os.environ.get("RAG_URL", "http://rag:8000")
    timeout: float = 600.0

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        """Retrieve verbatim chunks from the rag service for `hint`.

        `path` is not sent (RAG-Anything merges the corpus and cannot
        pre-filter — Scout post-filters, R-4.3). `scope` is accepted for
        interface parity but ignored: RAG-Anything cannot enforce role-based
        access; that is the V2 swap (R-4.8). The blocking HTTP call runs in a
        worker thread so it does not block the event loop.
        """
        chunks = await asyncio.to_thread(self._retrieve_sync, hint, k)
        return chunks

    def _retrieve_sync(self, hint: str, k: int) -> list[RagChunk]:
        body = json.dumps({"hint": hint, "k": k}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/retrieve",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            payload = json.load(resp)
        chunks = payload.get("chunks", [])
        return [self._to_chunk(c) for c in chunks if isinstance(c, dict)]

    @staticmethod
    def _to_chunk(c: dict[str, object]) -> RagChunk:
        raw_score = c.get("score", 0.0)
        score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
        loc_val = c.get("loc")
        ref = c.get("reference_id")
        return RagChunk(
            text=str(c.get("text", "")),
            file_path=str(c.get("file_path", "")),
            score=score,
            loc=str(loc_val) if loc_val is not None else None,
            meta={"reference_id": str(ref)} if ref is not None else {},
        )
