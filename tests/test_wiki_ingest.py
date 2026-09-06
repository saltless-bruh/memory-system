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

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        """Nothing is indexed yet, so nothing is skipped."""
        return []

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


# ── the replica publishes through a symlink ────────────────────────────────


def _snapshot_vault(root: Path, commit: str = "deadbeef") -> Path:
    """Build the replica's real shape: immutable snapshot + `current` symlink."""
    snapshot = root / "snapshots" / commit / "wiki"
    (snapshot / "concepts").mkdir(parents=True)
    (snapshot / "concepts" / "alpha.md").write_text(
        "---\ntitle: Alpha\ntype: concept\n---\n\n"
        "## TL;DR\n\nAlpha is a worked example.\n\n"
        "## Cross-References\n\n[[beta]]\n",
        encoding="utf-8",
    )
    (root / "current").symlink_to(Path("snapshots") / commit, target_is_directory=True)
    return root / "current" / "wiki"


@pytest.mark.asyncio
async def test_source_uri_is_relative_to_the_vault_even_behind_a_symlink(
    tmp_path: Path,
) -> None:
    """`host-sync` publishes `current -> snapshots/<commit>`, so WIKI_DIR is a
    path *through* a symlink. `vault.load_pages` returns resolved page paths, so
    a `base_dir` left unresolved is never a lexical prefix of them and every
    page is stored under its absolute snapshot path instead. That identity
    changes on every push: the corpus is duplicated, none of it is readable by
    `wiki_read`, and the whole vault is re-embedded each time.
    """
    wiki_dir = _snapshot_vault(tmp_path)

    results = await ingest_wiki(wiki_dir, dry_run=True, env={})

    assert [r["source_uri"] for r in results] == ["concepts/alpha.md"]


@pytest.mark.asyncio
async def test_source_uri_does_not_change_when_the_snapshot_does(
    tmp_path: Path,
) -> None:
    """A second push must update rows, not create a parallel corpus."""
    first = await ingest_wiki(
        _snapshot_vault(tmp_path / "a", "c1"), dry_run=True, env={}
    )
    second = await ingest_wiki(
        _snapshot_vault(tmp_path / "b", "c2"), dry_run=True, env={}
    )

    assert [r["source_uri"] for r in first] == [r["source_uri"] for r in second]


# ── not re-embedding what has not changed ──────────────────────────────────


class _CountingEmbedder:
    """A `_NamedEmbedder` that records how much work it was asked to do."""

    model = "gemini/gemini-embedding-001"
    dim = 1024

    def __init__(self) -> None:
        self.embedded: list[str] = []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [[float(index == 0) for index in range(self.dim)] for _ in texts]


class _ManifestConnection(_RecordingConnection):
    """A recording connection that also answers the "what is indexed" query."""

    def __init__(self, manifest: list[dict[str, object]] | None = None) -> None:
        super().__init__()
        self.manifest = manifest or []
        self.manifest_queries = 0

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.manifest_queries += 1
        return list(self.manifest)


def _one_page_vault(
    tmp_path: Path, body: str = "The page body drives retrieval."
) -> Path:
    wiki = tmp_path / "vault"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "indexed-page.md").write_text(
        "---\ntitle: Indexed Page\ntype: concept\nupdated: 2026-08-28\n---\n\n"
        f"# Indexed Page\n\n## TL;DR\n\n{body}\n\n## Cross-References\n\n[[other]]\n",
        encoding="utf-8",
    )
    return wiki


def _manifest_row(wiki: Path, **overrides: object) -> dict[str, object]:
    """The manifest row a previous ingest of `_one_page_vault` would have left."""
    from scout.capabilities import capability_fingerprint
    from scout.wiki_ingest import WIKI_CHUNK_POLICY

    page = wiki / "concepts" / "indexed-page.md"
    row: dict[str, object] = {
        "source_uri": "concepts/indexed-page.md",
        "capability_fingerprint": json.dumps(capability_fingerprint()),
        "chunks": 2,
        "hashes": 1,
        "content_hash": hashlib.sha256(page.read_bytes()).hexdigest(),
        "models": 1,
        "model": "gemini/gemini-embedding-001",
        "policies": 1,
        "chunk_policy": WIKI_CHUNK_POLICY,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_an_unchanged_page_is_not_re_embedded(tmp_path: Path) -> None:
    """Every cycle re-embedded the whole vault: 2303 chunks at gateway rates.

    The watcher fires on every publication, so an edit to one page paid to
    rebuild all 431. Nothing read `content_hash` back, though it has been
    written on every chunk since leaf-1.2.1.
    """
    wiki = _one_page_vault(tmp_path)
    embedder = _CountingEmbedder()
    connection = _ManifestConnection([_manifest_row(wiki)])

    results = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, connection),
        embedder=embedder,
        env={},
    )

    assert [r["status"] for r in results] == ["unchanged"]
    assert embedder.embedded == []
    assert connection.chunk_writes == []
    assert connection.chunk_deletes == 0


@pytest.mark.asyncio
async def test_an_edited_page_is_re_embedded(tmp_path: Path) -> None:
    """The control: the short-circuit must not swallow a real edit."""
    wiki = _one_page_vault(tmp_path)
    stale = _manifest_row(wiki, content_hash="0" * 64)
    embedder = _CountingEmbedder()

    results = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, _ManifestConnection([stale])),
        embedder=embedder,
        env={},
    )

    assert [r["status"] for r in results] == ["ingested_ok"]
    assert embedder.embedded != []


@pytest.mark.asyncio
async def test_a_page_embedded_by_another_model_is_re_embedded(
    tmp_path: Path,
) -> None:
    """Skipping here would leave two vector spaces in one index -- the F-2
    failure the model stamp exists to prevent."""
    wiki = _one_page_vault(tmp_path)
    other = _manifest_row(wiki, model="fastembed/bge-small-en-v1.5")
    embedder = _CountingEmbedder()

    results = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, _ManifestConnection([other])),
        embedder=embedder,
        env={},
    )

    assert [r["status"] for r in results] == ["ingested_ok"]
    assert embedder.embedded != []


@pytest.mark.asyncio
async def test_a_page_chunked_under_another_policy_is_re_embedded(
    tmp_path: Path,
) -> None:
    """Same bytes, different chunk boundaries, different retrieval."""
    wiki = _one_page_vault(tmp_path)
    embedder = _CountingEmbedder()

    results = await ingest_wiki(
        wiki,
        conn=cast(
            asyncpg.Connection,
            _ManifestConnection(
                [_manifest_row(wiki, chunk_policy="chars=800;tokens=200")]
            ),
        ),
        embedder=embedder,
        env={},
    )

    assert [r["status"] for r in results] == ["ingested_ok"]


@pytest.mark.asyncio
async def test_a_page_whose_parser_has_changed_is_re_embedded(
    tmp_path: Path,
) -> None:
    """A parser revision change means the stored sections are not what this
    process would produce, which is what `capability_fingerprint` records."""
    wiki = _one_page_vault(tmp_path)
    fingerprint = json.dumps({"schema_version": 1, "parser_revision": 1})
    embedder = _CountingEmbedder()

    results = await ingest_wiki(
        wiki,
        conn=cast(
            asyncpg.Connection,
            _ManifestConnection(
                [_manifest_row(wiki, capability_fingerprint=fingerprint)]
            ),
        ),
        embedder=embedder,
        env={},
    )

    assert [r["status"] for r in results] == ["ingested_ok"]


@pytest.mark.asyncio
async def test_a_page_with_disagreeing_chunks_is_re_embedded(tmp_path: Path) -> None:
    """A half-written document must be rebuilt, not trusted."""
    wiki = _one_page_vault(tmp_path)
    embedder = _CountingEmbedder()

    results = await ingest_wiki(
        wiki,
        conn=cast(
            asyncpg.Connection, _ManifestConnection([_manifest_row(wiki, hashes=2)])
        ),
        embedder=embedder,
        env={},
    )

    assert [r["status"] for r in results] == ["ingested_ok"]


@pytest.mark.asyncio
async def test_a_page_absent_from_the_index_is_embedded(tmp_path: Path) -> None:
    wiki = _one_page_vault(tmp_path)
    embedder = _CountingEmbedder()

    results = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, _ManifestConnection([])),
        embedder=embedder,
        env={},
    )

    assert [r["status"] for r in results] == ["ingested_ok"]
    assert embedder.embedded != []


@pytest.mark.asyncio
async def test_a_dry_run_reports_every_page_and_consults_nothing(
    tmp_path: Path,
) -> None:
    """A dry run has no connection, so it cannot and must not short-circuit."""
    wiki = _one_page_vault(tmp_path)

    results = await ingest_wiki(wiki, dry_run=True, env={})

    assert [r["status"] for r in results] == ["dry_run_ok"]


# ── control documents must not survive in the index ────────────────────────


@pytest.mark.asyncio
async def test_reconciliation_purges_control_documents(tmp_path: Path) -> None:
    """`ingest_wiki` refuses to chunk index.md and log.md; the index must agree.

    Measured on the live corpus: `log.md` held 208 chunks, 9.6% of everything
    searchable, left behind by an ingest that predates the exclusion. Seven of
    them matched one of the benchmark questions, so a changelog was competing
    with the page that actually answers it. The file is on disk, so a
    missing-file sweep never touches it.
    """
    purged: list[object] = []

    class _Connection:
        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            yield

        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            return [
                {"source_uri": "log.md", "doc_id": 1},
                {"source_uri": "index.md", "doc_id": 2},
                {"source_uri": "concepts/real-page.md", "doc_id": 3},
                {"source_uri": "concepts/deleted.md", "doc_id": 4},
            ]

        async def execute(self, query: str, *args: object) -> str:
            assert query.startswith("DELETE FROM rag_documents")
            purged.append(args[0])
            return "DELETE 1"

    from scout.wiki_ingest import reconcile_wiki_deletions

    wiki = tmp_path / "vault"
    (wiki / "concepts").mkdir(parents=True)
    for name in ("log.md", "index.md"):
        (wiki / name).write_text("# control\n", encoding="utf-8")
    (wiki / "concepts" / "real-page.md").write_text("# real\n", encoding="utf-8")

    deleted = await reconcile_wiki_deletions(
        wiki, conn=cast(asyncpg.Connection, _Connection())
    )

    assert deleted == ["concepts/deleted.md", "index.md", "log.md"]
    assert "concepts/real-page.md" not in deleted
    assert purged == [1, 2, 4]
