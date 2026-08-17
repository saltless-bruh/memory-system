"""RAG engine adapters. Each implements `scout.types.RagBackend`.

- `rag_anything.RagAnythingBackend` — V1 engine (lazy-imports LightRAG).
- `fake.FakeRagBackend` — in-memory backend for tests + as the reference
  proof that Scout core works against any adapter.
- `pgvector.PgVectorRlsBackend` — V2 stub (R2R alternative). Its mere
  existence with zero RAG-Anything imports proves core is engine-agnostic.
"""
