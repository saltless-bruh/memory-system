# Project Roadmap — SNP Memory System

The active architecture uses one PostgreSQL hybrid index and one Scout
retrieval contract:

1. `wiki_search` ranks distinct wiki pages from body-derived chunks.
2. `wiki_read` returns a canonical page envelope at selectable granularity.
3. Scope is resolved from the verified caller and threaded through both calls.
4. Wiki and local source ingestion share the configured 1024-dimensional cloud
   embedding route.

Current follow-on work includes sync rename/delete reconciliation, an index
inspector, complete V3 vault verification, external source fetch/cache, and
rebuild reproducibility. Do not present a deferred subsystem as deployed.

Use `docs/ARCHITECTURE_STATUS.md`, AGENTS.md, and the active unlazy plan for
current sequencing. Historical roadmaps are evidence, not operating authority.
