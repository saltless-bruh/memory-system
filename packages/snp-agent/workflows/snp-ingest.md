---
description: Ingests a supported PDF, text/Markdown, CSV/TSV, code, or image artifact into the PostgreSQL 16 pgvector Data Vault.
---

# /snp-ingest

Execute the following ingestion protocol:

1. **Place and Ingest**:
   - Copy the target file into `./raw/<category>/<filename>`.
   - Let `sync-job` reconcile it, or run
     `uv run python scripts/ingest_v2.py --path raw/<category>/<filename> --dept <department>`.
   - Ingestion runs as `rag_ingest_role`; never substitute a migration-admin
     credential. No central ingest REST endpoint is implemented.

2. **Verify Database Chunks**:
   - Confirm that the file is indexed and embeddings are stored in PostgreSQL 16 `pgvector`.

3. **Prompt Note Compilation**:
   - Prompt the user: *"File successfully indexed. Would you like me to run `/snp-compile` to author its Knowledge Vault note?"*
