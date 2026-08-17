#!/usr/bin/env python3
"""SNP Memory System V2 Multi-Modal Ingestion Pipeline CLI.

CLI wrapper over `scout.ingest`.
"""

from __future__ import annotations

from scout.ingest import (
    get_pg_connection,
    ingest_directory,
    ingest_document,
    main,
    reconcile_deletions,
)

__all__ = [
    "get_pg_connection",
    "ingest_directory",
    "ingest_document",
    "main",
    "reconcile_deletions",
]

if __name__ == "__main__":
    main()
