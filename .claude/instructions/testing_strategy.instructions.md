# Testing Strategy — SNP Memory System

1. Run deterministic unit tests with
   `timeout 300s uv run pytest -m 'not integration' --disable-socket -q`.
2. Run marked integration tests separately with explicit socket enablement and
   only against the disposable integration Compose project.
3. Cover retrieval invariants directly: page deduplication, canonical reads,
   required scope propagation, sparse degradation, model stamps, and the exact
   MCP tool surface.
4. Use positive controls for absence gates so a detector is observed catching
   the behavior it prohibits.
5. Treat unavailable dependencies as infrastructure failures, never passes.
6. The full V3 page-schema verifier is still pending. Until it exists, keep
   schema/heading review explicit and do not relabel a narrower legacy check.
