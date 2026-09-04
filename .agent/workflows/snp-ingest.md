---
description: Indexes an authorized local source or wiki checkout through the V3 body-chunk ingestion path and verifies scoped retrieval.
---

# /snp-ingest

1. Place the unchanged local artifact beneath `raw/`, or identify the mounted
   wiki checkout to synchronize.
2. Use `rag_ingest_role` and canonical department ACLs; never substitute the
   migration administrator or query identity.
3. Let sync-job reconcile the path, or run the repository's explicit local
   ingestion command when the operator requested it.
4. Confirm body-derived chunks were stored with the configured embedding model
   and dimension. Zero chunks is a failure.
5. Run a scoped `wiki_search` query using body vocabulary, then `wiki_read` the
   intended page when this is a wiki ingestion.

External URL fetch/cache and on-demand source extraction are deferred. Merely
recording a URL does not download or index its content. Report that boundary
and do not claim the external evidence is searchable.
