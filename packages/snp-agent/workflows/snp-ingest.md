---
description: Ingests a new raw document (PDF, CSV, RFC, Code, DOCX) into the PostgreSQL 16 pgvector Data Vault.
---

# /snp-ingest

Execute the following ingestion protocol:

1. **Topology Detection**:
   - **Local Solo Dev Mode**: If running locally with Docker/Postgres:
     - Copy the target file into `./raw/<category>/<filename>`.
     - Run `uv run python scripts/ingest_v2.py --path raw/<category>/<filename> --dept <department>`.
   - **Team Enterprise Mode**: If connecting to a central SNP server:
     - Dispatch the file to the Central Ingest REST API:
       ```bash
       curl -fsSL -X POST https://api.snp.internal/v2/ingest \
         -H "Authorization: Bearer $SNP_API_TOKEN" \
         -F "file=@<path_to_file>" \
         -F "department=<department>"
       ```

2. **Verify Database Chunks**:
   - Confirm that the file is indexed and embeddings are stored in PostgreSQL 16 `pgvector`.

3. **Prompt Note Compilation**:
   - Prompt the user: *"File successfully indexed. Would you like me to run `/snp-compile` to author its Knowledge Vault note?"*
