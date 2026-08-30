"""Contracts for the V3 wiki body-ingestion path."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import asyncpg
import pytest

from scout.chunker import ContextualChunk, ContextualChunker
from scout.parsers import parse_markdown
from scout.wiki_ingest import (
    WIKI_ALLOWED_DEPARTMENTS,
    IndexCatalog,
    WikiIngestError,
    ingest_wiki,
    load_index_catalog,
    prepare_wiki_document,
)


def _chunks(
    markdown: str, catalog: IndexCatalog | None = None
) -> list[ContextualChunk]:
    parsed = parse_markdown(markdown, "concepts/example.md")
    prepared = prepare_wiki_document(parsed, catalog or IndexCatalog.empty())
    return ContextualChunker(max_chunk_chars=1400, overlap_chars=0).chunk_document(
        prepared
    )


def test_body_without_summary_is_indexed_and_tldr_is_chunk_zero() -> None:
    chunks = _chunks(
        """---
title: Body Route
type: concept
updated: 2026-08-28
---

# Body Route

Lead paragraph comes from the page body.

## TL;DR

Explicit routing prose is also body evidence.

## Details

BODY_ONLY_SENTINEL is searchable even though summary is absent.
"""
    )

    assert chunks
    assert chunks[0].chunk_index == 0
    assert chunks[0].metadata["role"] == "tldr"
    assert chunks[0].metadata["tldr_source"] == "section"
    assert all(chunk.metadata["corpus"] == "wiki" for chunk in chunks)
    assert all(chunk.metadata["type"] == "concept" for chunk in chunks)
    assert any("BODY_ONLY_SENTINEL" in chunk.contextual_text for chunk in chunks)
    assert all(chunk.chunk_text.strip() for chunk in chunks)


def test_summary_only_page_is_rejected_as_body_evidence() -> None:
    parsed = parse_markdown(
        """---
title: Summary Only
type: concept
summary: A plausible routing sentence that must not be embedded.
updated: 2026-08-28
---
""",
        "concepts/summary-only.md",
    )

    with pytest.raises(WikiIngestError, match="no retrievable body text"):
        prepare_wiki_document(parsed, IndexCatalog.empty())


def test_tldr_resolution_uses_first_matching_rung_and_rejects_stale_index() -> None:
    catalog = IndexCatalog(
        updated=dt.date(2026, 8, 20),
        descriptions={"concepts/example": "Fresh authored catalogue description."},
    )

    explicit = _chunks(
        """---
title: Example
updated: 2026-08-01
---
# Example

Lead loses to an explicit TLDR.

## TL;DR
Explicit wins.

## Detail
First detail sentence. Second detail sentence.
""",
        catalog,
    )[0]
    assert explicit.metadata["tldr"] == "Explicit wins."
    assert explicit.metadata["tldr_source"] == "section"

    lead = _chunks(
        """---
title: Example
updated: 2026-08-01
---
# Example

Lead wins before the catalogue.

## Detail
First detail sentence. Second detail sentence.
""",
        catalog,
    )[0]
    assert lead.metadata["tldr"] == "Lead wins before the catalogue."
    assert lead.metadata["tldr_source"] == "lead"

    indexed = _chunks(
        """---
title: Example
updated: 2026-08-01
---
# Example

## Detail
First detail sentence. Second detail sentence. Third detail sentence.
""",
        catalog,
    )[0]
    assert indexed.metadata["tldr"] == "Fresh authored catalogue description."
    assert indexed.metadata["tldr_source"] == "index"

    stale = _chunks(
        """---
title: Example
updated: 2026-08-21
---
# Example

## Detail
Current first sentence. Current second sentence. Current third sentence.
""",
        catalog,
    )[0]
    assert stale.metadata["tldr"] == (
        "Current first sentence. Current second sentence."
    )
    assert stale.metadata["tldr_source"] == "inferred"

    undated = _chunks(
        """---
title: Example
---
# Example

## Detail
Undated first sentence. Undated second sentence. Undated third sentence.
""",
        catalog,
    )[0]
    assert undated.metadata["tldr"] == (
        "Undated first sentence. Undated second sentence."
    )
    assert undated.metadata["tldr_source"] == "inferred"


def test_index_catalog_harvests_markdown_links_and_wikilinks(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    index.write_text(
        """---
title: Catalogue
updated: 2026-08-20
---

# Catalogue

- [Alpha](concepts/alpha.md) — Alpha description.
- [[entities/beta|Beta]] — Beta description.
- [No description](concepts/empty.md)
""",
        encoding="utf-8",
    )

    catalog = load_index_catalog(index)

    assert str(catalog.updated) == "2026-08-20"
    assert catalog.description_for("concepts/alpha.md", "Alpha") == (
        "Alpha description."
    )
    assert catalog.description_for("entities/beta.md", "Beta") == ("Beta description.")
    assert catalog.description_for("concepts/empty.md", "No description") is None


def test_cross_reference_links_become_metadata_without_erasing_prose() -> None:
    chunks = _chunks(
        """---
title: Link Handling
type: concept
---
# Link Handling

## TL;DR
Link handling keeps explanatory prose.

## Cross-References
- [[pure-one]]
[[pure-two]], [[pure-three]]
See [[kept-link]] for the mitigation path because it contradicts this page.
"""
    )

    embedded = "\n".join(chunk.chunk_text for chunk in chunks)
    assert "- [[pure-one]]" not in embedded
    assert "[[pure-two]], [[pure-three]]" not in embedded
    assert "See [[kept-link]] for the mitigation path" in embedded
    assert chunks[0].metadata["wikilinks"] == [
        "pure-one",
        "pure-two",
        "pure-three",
        "kept-link",
    ]


class _NamedEmbedder:
    model = "gemini/gemini-embedding-001"
    dim = 1024

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(index == 0) for index in range(self.dim)] for _ in texts]


class _RecordingConnection:
    def __init__(self) -> None:
        self.document_writes: list[tuple[object, ...]] = []
        self.chunk_writes: list[tuple[object, ...]] = []
        self.chunk_deletes = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchrow(self, query: str, *args: object) -> dict[str, int]:
        assert "ON CONFLICT (source_uri) DO UPDATE" in query
        self.document_writes.append(args)
        return {"doc_id": 7}

    async def execute(self, query: str, *args: object) -> str:
        if query.startswith("DELETE FROM rag_chunks"):
            self.chunk_deletes += 1
            return "DELETE 1"
        if "INSERT INTO rag_chunks" in query:
            self.chunk_writes.append(args)
            return "INSERT 0 1"
        raise AssertionError(f"unexpected SQL: {query}")

    async def close(self) -> None:
        raise AssertionError("caller-owned connection must not be closed")


@pytest.mark.asyncio
async def test_wiki_ingest_uses_all_departments_and_stamps_model_and_dim(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "vault"
    page_dir = wiki / "concepts"
    page_dir.mkdir(parents=True)
    (wiki / "index.md").write_text(
        """---
title: Catalogue
updated: 2026-08-28
---
- [Indexed Page](concepts/indexed-page.md) — Authored index description.
""",
        encoding="utf-8",
    )
    page_path = page_dir / "indexed-page.md"
    page_path.write_text(
        """---
title: Indexed Page
type: concept
updated: 2026-08-28
sources: [https://example.test/source]
---
# Indexed Page

## TL;DR
The page body drives retrieval.

## Detail
DATABASE_BODY_SENTINEL survives through the SQL insert.

## Provenance
The page was compiled from the declared source.

## Cross-References
- [[metadata-only-link]]
Prose about [[retained-link]] stays searchable.
""",
        encoding="utf-8",
    )
    connection = _RecordingConnection()

    first = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, connection),
        embedder=_NamedEmbedder(),
    )
    second = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, connection),
        embedder=_NamedEmbedder(),
    )

    assert first[0]["status"] == second[0]["status"] == "ingested_ok"
    assert len(connection.document_writes) == 2
    assert connection.chunk_deletes == 2
    assert connection.document_writes[0][0] == "concepts/indexed-page.md"
    assert connection.document_writes[0][1] == list(WIKI_ALLOWED_DEPARTMENTS)

    first_run_chunks = connection.chunk_writes[: len(connection.chunk_writes) // 2]
    assert first_run_chunks
    assert [write[1] for write in first_run_chunks] == list(
        range(len(first_run_chunks))
    )
    assert any("DATABASE_BODY_SENTINEL" in str(write[2]) for write in first_run_chunks)
    assert all(
        "- [[metadata-only-link]]" not in str(write[2]) for write in first_run_chunks
    )
    metadata_rows = [json.loads(cast(str, write[5])) for write in first_run_chunks]
    expected_hash = hashlib.sha256(page_path.read_bytes()).hexdigest()
    assert metadata_rows[0]["role"] == "tldr"
    assert {row["role"] for row in metadata_rows} == {
        "tldr",
        "section",
        "provenance",
    }
    assert all(row["corpus"] == "wiki" for row in metadata_rows)
    assert all(row["model"] == _NamedEmbedder.model for row in metadata_rows)
    assert all(row["dim"] == _NamedEmbedder.dim for row in metadata_rows)
    assert all(row["content_hash"] == expected_hash for row in metadata_rows)
    assert all(row["tldr_source"] == "section" for row in metadata_rows)
    assert all(row["summary"] == "Authored index description." for row in metadata_rows)
    assert all(
        row["sources"] == ["https://example.test/source"] for row in metadata_rows
    )
    assert all(
        row["wikilinks"] == ["metadata-only-link", "retained-link"]
        for row in metadata_rows
    )
