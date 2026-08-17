---
name: snp-ingest-raw-data
description: >-
  Use this skill when you are asked to add new raw documents, spreadsheets, images, or reports into the memory system so they can be processed by the RAG engine.
---

# snp-ingest-raw-data

## Purpose
The SNP Memory System V1 features an Enterprise Data Parsing pipeline in `rag/app.py` capable of reading nested `.xlsx` spreadsheets, `.docx` files, and performing OCR on images. This skill teaches you how to correctly place files for ingestion.

## How to use

1. **Place the Files in the Raw Store**
   All original documents must be placed somewhere inside the `raw/` directory. Create logical subdirectories if necessary (e.g., `raw/reports/`, `raw/rfcs/`, `raw/spreadsheets/`).
   
   Example:
   ```bash
   cp ~/Downloads/financial_Q3.xlsx raw/reports/
   ```

2. **Allow the RAG Engine to Process**
   Once the file is saved to disk in the `raw/` directory, the background `RAG-Anything` engine (and `rag/app.py` wrappers) will automatically pick it up, extract text/tables/images, chunk it, and rebuild the Knowledge Graph (`rag_storage/`).
   You do not need to manually run the RAG engine.

3. **Compile the Wiki Page**
   After placing the raw file, you MUST create a compiled Wiki page for it so humans can route to it. 
   Use the `snp-compile-wiki` skill on the newly added file path to mint its address and generate its markdown note.
