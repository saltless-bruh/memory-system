---
name: snp-ingest-raw-data
description: "Use this skill when a user asks to place and index a supported local source artifact or wiki checkout through the V3 ingestion pipeline."
---

# Ingest local evidence

## Supported current path

1. Put an authorized local artifact beneath `raw/` without changing its bytes.
2. Assign one or more canonical departments and use the ingestion identity,
   never the migration administrator or query identity.
3. Let sync-job reconcile it, or invoke the repository's explicit ingestion
   command for that path.
4. Confirm the document produced body-derived chunks with the configured model
   stamp. A zero-chunk result is a failure, not a successful empty document.
5. Query for vocabulary present in the body and confirm the intended document
   is retrievable within the authorized scope.

Markdown wiki pages are chunked by heading and indexed as the `wiki` corpus.
Other supported local documents retain their format-specific parser behavior.

## Deferred external-source path

External URL fetch, immutable caching, digest recording, and on-demand source
extraction are not implemented yet. Do not pretend that placing a URL in page
metadata downloads or indexes it. Record the pending source and tell the user
that the fetch queue must land before its contents can be retrieved.

After evidence is indexed, offer the separate page-compilation workflow only
when the user wants a wiki change.
