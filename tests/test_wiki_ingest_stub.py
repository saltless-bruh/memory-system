"""A vault page edited down to a bodiless stub must stop being searchable.

`prepare_wiki_document` refuses a page with no body, and it does so inside
`ingest_document` *before* the branch that purges a source's earlier rows. The
refusal was caught, reported as `skipped_no_body`, and nothing was deleted, so
`wiki_search` went on routing to -- and snippeting -- text the page no longer
says, on every cycle, for as long as the stub stayed on disk.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import asyncpg
import pytest

from scout.wiki_ingest import WIKI_CHUNK_POLICY, ingest_wiki

_PAGE = "concepts/edited-page.md"


class _Embedder:
    model = "gemini/gemini-embedding-001"
    dim = 1024

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("a bodiless page has nothing to embed")


class _IndexedConnection:
    """Answers the manifest query with whatever an earlier ingest left behind."""

    def __init__(self, manifest: list[dict[str, object]]) -> None:
        self.manifest = manifest
        self.deleted: list[tuple[object, ...]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        return list(self.manifest)

    async def execute(self, query: str, *args: object) -> str:
        if query.lstrip().startswith("DELETE FROM rag_documents"):
            self.deleted.append(args)
            indexed = {row["source_uri"] for row in self.manifest}
            return "DELETE 1" if args[0] in indexed else "DELETE 0"
        raise AssertionError(f"unexpected SQL: {query}")

    async def close(self) -> None:
        raise AssertionError("caller-owned connection must not be closed")


def _stub_vault(tmp_path: Path) -> Path:
    """One page whose body has been deleted, leaving only its frontmatter."""
    wiki = tmp_path / "vault"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / _PAGE).write_text(
        "---\ntitle: Edited Page\ntype: concept\n---\n", encoding="utf-8"
    )
    return wiki


def _previously_indexed() -> dict[str, object]:
    """The signature the page's former, body-bearing revision left in the index."""
    from scout.capabilities import capability_fingerprint

    return {
        "source_uri": _PAGE,
        "capability_fingerprint": json.dumps(capability_fingerprint()),
        "chunks": 3,
        "hashes": 1,
        "content_hash": hashlib.sha256(b"the old body").hexdigest(),
        "models": 1,
        "model": _Embedder.model,
        "policies": 1,
        "chunk_policy": WIKI_CHUNK_POLICY,
    }


@pytest.mark.asyncio
async def test_a_page_edited_down_to_a_stub_loses_its_old_chunks(
    tmp_path: Path,
) -> None:
    wiki = _stub_vault(tmp_path)
    connection = _IndexedConnection([_previously_indexed()])

    results = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, connection),
        embedder=_Embedder(),
        env={},
    )

    assert [args[0] for args in connection.deleted] == [_PAGE]
    # The delete is scoped to the vault tier: a raw-corpus row that happened to
    # share the address must never be reachable from this sweep.
    assert all("wiki" in args for args in connection.deleted)
    [result] = results
    assert result["source_uri"] == _PAGE
    assert result["status"] == "purged_empty"
    assert result["chunks_count"] == 0


@pytest.mark.asyncio
async def test_a_stub_that_was_never_indexed_is_skipped_without_a_delete(
    tmp_path: Path,
) -> None:
    """The control: nothing was published, so there is nothing to withdraw."""
    wiki = _stub_vault(tmp_path)
    connection = _IndexedConnection([])

    results = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, connection),
        embedder=_Embedder(),
        env={},
    )

    assert connection.deleted == []
    assert [r["status"] for r in results] == ["skipped_no_body"]


@pytest.mark.asyncio
async def test_a_dry_run_over_a_stub_deletes_nothing(tmp_path: Path) -> None:
    wiki = _stub_vault(tmp_path)

    results = await ingest_wiki(wiki, embedder=_Embedder(), dry_run=True, env={})

    assert [r["status"] for r in results] == ["skipped_no_body"]


@pytest.mark.asyncio
async def test_a_dry_run_with_a_supplied_connection_deletes_nothing(
    tmp_path: Path,
) -> None:
    """A dry run may be handed a connection to read the manifest -- the ingest
    policy gate does exactly that -- and must still leave the index untouched,
    even over a stub whose former body is indexed.
    """
    wiki = _stub_vault(tmp_path)
    connection = _IndexedConnection([_previously_indexed()])

    results = await ingest_wiki(
        wiki,
        conn=cast(asyncpg.Connection, connection),
        embedder=_Embedder(),
        dry_run=True,
        env={},
    )

    assert connection.deleted == []
    assert [r["status"] for r in results] == ["skipped_no_body"]
