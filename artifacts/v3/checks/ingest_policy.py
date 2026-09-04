#!/usr/bin/env python3
"""Oracles for ingest content policy gates.

Verifies that:
1. Control documents (index.md, log.md) produce no chunks
2. Markdown tables are never split across chunks
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def group_control_docs_excluded() -> str:
    """Verify that index.md and log.md produce zero chunks while an ordinary page does.

    A control document must never become a retrieval candidate, but the gate must
    not pass vacuously. Tests both absolute and relative wiki_dir paths to verify
    path resolution is correct (a common defect when tempfile produces absolute paths
    but configuration provides relative ones).
    """
    sys.path.insert(0, str(REPO_ROOT))
    from scout.wiki_ingest import ingest_wiki  # noqa: PLC0415

    ordinary_page = """---
title: Ordinary Page
type: concept
updated: 2026-08-28
---

# Ordinary Page

This is an ordinary page with body content that should be indexed.

## Details

This page should produce chunks for retrieval.
"""

    # index.md should only be used for catalog descriptions, not chunks
    index_doc = """---
title: Catalogue
updated: 2026-08-28
---

# Catalogue

- [Ordinary Page](concepts/ordinary.md) — An ordinary page in the vault.
"""

    # log.md is an authored control document, like a changelog
    log_doc = """---
title: Editorial Log
updated: 2026-08-28
---

# Editorial Log

## 2026-08-28

- Updated Ordinary Page with new details.
- Fixed formatting in another page.

## 2026-08-27

- Created Ordinary Page.
- Initial vault structure.
"""

    async def run_ingest_test(
        wiki_path: Path, path_description: str
    ) -> tuple[int, int, int, int]:
        """Run ingest and count chunks, returning (index, root_log, nested_log, ordinary)."""

        class CountingEmbedder:
            """Embedder that tracks what gets embedded."""

            model = "test/test-embed"
            dim = 128

            def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
                return [[0.0] * self.dim for _ in texts]

        class CountingConnection:
            """Connection that tracks chunk inserts."""

            def __init__(self) -> None:
                self.chunks_by_source: dict[str, int] = {}

            async def transaction(self) -> Any:
                class DummyTransaction:
                    async def __aenter__(self) -> Any:
                        return self

                    async def __aexit__(self, *args: Any) -> None:
                        pass

                return DummyTransaction()

            async def fetchrow(self, _query: str, *_args: Any) -> dict[str, int]:
                return {"doc_id": 1}

            async def execute(self, query: str, *args: Any) -> str:
                if query.strip().startswith("DELETE FROM rag_chunks"):
                    return "DELETE 0"
                if "INSERT INTO rag_chunks" in query:
                    source_uri = str(args[0]) if args else "unknown"
                    self.chunks_by_source[source_uri] = (
                        self.chunks_by_source.get(source_uri, 0) + 1
                    )
                    return "INSERT 0 1"
                raise GateFailure(f"unexpected SQL: {query}")

            async def close(self) -> None:
                pass

        embedder = CountingEmbedder()
        connection = CountingConnection()

        results = await ingest_wiki(
            wiki_path, conn=cast(Any, connection), embedder=embedder, dry_run=True
        )

        index_chunks = 0
        root_log_chunks = 0
        nested_log_chunks = 0
        ordinary_chunks = 0

        # Resolve wiki_path to get expected base for normalization
        resolved_wiki = wiki_path.resolve()  # noqa: ASYNC240

        for result in results:
            source = result.get("source_uri", "")
            chunks = result.get("chunks_count", 0)
            # Normalize source to relative path for comparison
            try:
                rel_source = Path(source).relative_to(resolved_wiki).as_posix()
            except ValueError:
                rel_source = source

            if rel_source == "index.md":
                index_chunks += chunks
            elif rel_source == "log.md":
                root_log_chunks += chunks
            elif rel_source == "concepts/log.md":
                nested_log_chunks += chunks
            elif "ordinary" in rel_source:
                ordinary_chunks += chunks

        return index_chunks, root_log_chunks, nested_log_chunks, ordinary_chunks

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wiki = tmp_path / "vault"
        concepts_dir = wiki / "concepts"
        concepts_dir.mkdir(parents=True)

        # Write control documents
        (wiki / "index.md").write_text(index_doc, encoding="utf-8")
        (wiki / "log.md").write_text(log_doc, encoding="utf-8")

        # Write an ordinary page
        (concepts_dir / "ordinary.md").write_text(ordinary_page, encoding="utf-8")

        # Write a nested log.md at concepts/log.md that SHOULD be ingested
        nested_log = """---
title: Nested Log
type: concept
updated: 2026-08-28
---

# Nested Log

This is a log.md at a nested path, not the root control document.
It should be ingested normally.
"""
        (concepts_dir / "log.md").write_text(nested_log, encoding="utf-8")

        # Test 1: absolute path (tempfile always provides this)
        index_c, root_c, nested_c, ord_c = asyncio.run(
            run_ingest_test(wiki, "absolute")
        )
        require(
            index_c == 0,
            f"[absolute] index.md produced {index_c} chunks, expected 0",
        )
        require(
            root_c == 0,
            f"[absolute] root log.md produced {root_c} chunks, expected 0",
        )
        require(
            nested_c > 0,
            f"[absolute] nested concepts/log.md produced {nested_c} chunks, "
            "exclusion must be scoped to root-level log.md only",
        )
        require(
            ord_c > 0,
            f"[absolute] ordinary page produced {ord_c} chunks; "
            "positive control failed",
        )

        # Test 2: relative path (production configuration sets WIKI_DIR=./wiki)
        # Change to temp directory and pass relative path
        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            rel_wiki = Path("vault")

            index_c, root_c, nested_c, ord_c = asyncio.run(
                run_ingest_test(rel_wiki, "relative")
            )
            require(
                index_c == 0,
                f"[relative] index.md produced {index_c} chunks, expected 0; "
                "exclusion fails when wiki_dir is relative (path not resolved)",
            )
            require(
                root_c == 0,
                f"[relative] root log.md produced {root_c} chunks, expected 0; "
                "exclusion fails when wiki_dir is relative (path not resolved)",
            )
            require(
                nested_c > 0,
                f"[relative] nested concepts/log.md produced {nested_c} chunks, "
                "exclusion must be scoped to root-level log.md only",
            )
            require(
                ord_c > 0,
                f"[relative] ordinary page produced {ord_c} chunks; "
                "positive control failed",
            )
        finally:
            os.chdir(original_cwd)

    return "CONTROL DOCS EXCLUDED VERIFIED"


def group_table_atomic() -> str:
    """Verify that Markdown tables are atomic and cannot be split across chunks.

    A table larger than the chunk size target should stay whole in one chunk,
    and must carry its enclosing heading in context_prefix. The positive control
    ensures that equally long ordinary prose still splits into multiple chunks,
    so the gate does not pass merely because we raised the chunk size limit.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from scout.chunker import ContextualChunker  # noqa: PLC0415
    from scout.parsers import parse_markdown  # noqa: PLC0415
    from scout.wiki_ingest import (  # noqa: PLC0415
        IndexCatalog,
        prepare_wiki_document,
    )

    # Build a table larger than typical chunk limits
    # WIKI_TARGET_CHUNK_TOKENS = 350, WIKI_MAX_CHUNK_CHARS = 1400
    table_rows = []
    for i in range(20):
        table_rows.append(
            f"| Row {i:02d} | Data Column A {i:03d} | Data Column B {i:03d} |"
        )

    table_header = "| Row # | Column A | Column B |"
    table_separator = "|-------|----------|----------|"

    markdown_table = "\n".join([table_header, table_separator] + table_rows)

    # Create a page with a table section
    table_page = f"""---
title: Table Page
type: concept
updated: 2026-08-28
---

# Table Page

This page contains a table.

## Data Table

{markdown_table}

## After Table

Some prose after the table to ensure the table itself is the focus.
"""

    # Create a control page with equally long ordinary prose
    # The table has ~20 rows with 3 columns, totaling roughly 400+ chars
    # We need enough prose to exceed max_chunk_chars (1400) to force splitting
    prose_lines = []
    word = "word"
    for _ in range(350):  # Enough to exceed 1400 chars and force splitting
        prose_lines.append(word)

    ordinary_prose = " ".join(prose_lines)

    prose_page = f"""---
title: Prose Page
type: concept
updated: 2026-08-28
---

# Prose Page

{ordinary_prose}
"""

    # Test table atomicity
    parsed_table = parse_markdown(table_page, "concepts/table.md")
    prepared_table = prepare_wiki_document(parsed_table, IndexCatalog.empty())
    chunker = ContextualChunker(max_chunk_chars=1400, overlap_chars=0)
    table_chunks = chunker.chunk_document(prepared_table)

    # Find the table section's chunks
    table_section_chunks = [
        chunk
        for chunk in table_chunks
        if "Data Table" in (chunk.metadata.get("heading") or "")
    ]

    require(
        bool(table_section_chunks),
        "table section produced no chunks (page ingestion might have failed)",
    )

    # Verify the table is not split
    # Count how many chunks contain table markers
    table_marker_chunks = [
        chunk for chunk in table_section_chunks if "|" in chunk.chunk_text
    ]
    require(
        len(table_marker_chunks) == 1,
        f"table spans {len(table_marker_chunks)} chunks; "
        "D-5 requires tables to be atomic (not split across chunks)",
    )

    # Verify the table chunk carries its heading in context
    table_chunk = table_marker_chunks[0]
    require(
        "Data Table" in table_chunk.context_prefix,
        f"table chunk's context_prefix does not include its heading; "
        f"got: {table_chunk.context_prefix!r}",
    )

    # Control: verify that prose of similar length DOES split
    parsed_prose = parse_markdown(prose_page, "concepts/prose.md")
    prepared_prose = prepare_wiki_document(parsed_prose, IndexCatalog.empty())
    prose_chunks = chunker.chunk_document(prepared_prose)

    # Count chunks in the main section (not tldr)
    prose_body_chunks = [
        chunk for chunk in prose_chunks if chunk.metadata.get("role") != "tldr"
    ]

    require(
        len(prose_body_chunks) > 1,
        f"prose control produced {len(prose_body_chunks)} chunk(s); "
        "expected multiple chunks to prove the chunker still splits prose. "
        "Without this control, the gate would pass if we simply raised chunk size limits.",
    )

    return "TABLE ATOMICITY VERIFIED"


def group_control_docs_still_readable() -> str:
    """Verify that control documents remain readable through wiki_read after exclusion.

    The exclusion from chunk ingestion must not break load_pages or wiki_read slug
    resolution. log.md must still be returned by load_pages and resolvable through
    wiki_read by slug (not only by full path), so that direct queries still work.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from scout import vault  # noqa: PLC0415
    from scout.diy_engine import ScoutDiyEngine  # noqa: PLC0415
    from scout.types import RagChunk, Scope  # noqa: PLC0415

    log_doc = """---
title: Editorial Log
updated: 2026-08-28
---

# Editorial Log

## 2026-08-28

- Created test vault.
- Initial structure.
"""

    with tempfile.TemporaryDirectory() as tmp:
        wiki = Path(tmp) / "vault"
        wiki.mkdir()

        # Write log.md to the vault
        (wiki / "log.md").write_text(log_doc, encoding="utf-8")

        # Create a concepts directory with one page
        (wiki / "concepts").mkdir()
        (wiki / "concepts" / "test.md").write_text(
            """---
title: Test Page
type: concept
updated: 2026-08-28
---

# Test Page

Test content.
""",
            encoding="utf-8",
        )

        # Assertion 1: log.md is still returned by load_pages
        pages = vault.load_pages(wiki)
        page_names = {p.path.name for p in pages}
        require(
            "log.md" in page_names,
            f"log.md missing from load_pages; got {page_names!r}; "
            "control documents must stay readable for wiki_read",
        )

        # Assertion 2: log.md is resolvable through wiki_read by slug
        class EmptyBackend:
            async def retrieve(
                self,
                _hint: str,
                *,
                path: str | None = None,
                scope: Scope | None = None,
                k: int = 10,
            ) -> Sequence[RagChunk]:
                del path, scope, k
                return ()

        scope = Scope(departments=frozenset({"infra"}))

        async def exercise() -> bool:
            engine = ScoutDiyEngine.from_vault(
                None,  # type: ignore[arg-type]
                wiki_dir=wiki,
                rag_backend=EmptyBackend(),
            )
            try:
                # Try to resolve by slug (direct path resolution at diy_engine.py:398)
                page = await engine.wiki_read("log", scope=scope)
                return bool(page and page.body)
            except (KeyError, ValueError):
                return False

        log_readable = asyncio.run(exercise())
        require(
            log_readable,
            "wiki_read cannot resolve 'log' by slug; "
            "the exclusion broke direct page access",
        )

    return "CONTROL DOCS STILL READABLE VERIFIED"


GROUPS: dict[str, Callable[[], str]] = {
    "control-docs-excluded": group_control_docs_excluded,
    "table-atomic": group_table_atomic,
    "control-docs-still-readable": group_control_docs_still_readable,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, choices=sorted(GROUPS))
    args = parser.parse_args()

    try:
        token = GROUPS[args.group]()
    except GateFailure as exc:
        print(f"FAIL [{args.group}] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR [{args.group}] {type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback  # noqa: PLC0415

        traceback.print_exc()
        return 2

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
