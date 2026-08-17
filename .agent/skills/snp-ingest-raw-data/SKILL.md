---
name: snp-ingest-raw-data
description: >-
  Use this skill when adding new raw documents, PDFs, RFCs, spreadsheets, or source code files into the Data Vault so they are indexed into PostgreSQL 16 pgvector.
---

# snp-ingest-raw-data

## Purpose
The SNP Memory System V2 processes multi-format raw artifacts (PDFs, Markdown RFCs, CSV spreadsheets, source code) and indexes them into **PostgreSQL 16 + pgvector** with HNSW indexing, full-text tsvector search, and Row-Level Security (RLS).

## How to use

1. **Place Files in the Raw Warehouse (`raw/`)**
   All original unstructured files must reside under the `raw/` directory. Organize into logical directories (e.g., `raw/architecture/`, `raw/reports/`, `raw/data/`, `raw/code/`, `raw/runbooks/`).
   
   Example:
   ```bash
   cp ~/Downloads/vllm_benchmark_report.pdf raw/reports/
   ```

2. **Ingest into PostgreSQL Data Vault**
   - **Automatic (Nhịp A Daemon)**: When running in Docker, the `sync-job` container continuously watches `raw/` and auto-ingests any new or modified file into PostgreSQL.
   - **Direct CLI Ingestion**: To immediately ingest from the command line without waiting:
     ```bash
     uv run python scripts/ingest_v2.py
     ```

3. **Verify Database Ingestion**
   Run address verification to ensure the indexed chunks are queryable:
   ```bash
   uv run python scripts/verify_addresses.py
   ```

4. **Compile the Wiki Knowledge Page**
   After raw data is indexed, synthesize a structured Wiki note so humans and agents can navigate to it. Use the `snp-compile-wiki` skill to mint a verifiable address and write the note.
