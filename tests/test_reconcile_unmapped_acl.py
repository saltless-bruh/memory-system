"""Revoking an ACL rule must purge rows whose files are still on disk.

`reconcile_deletions` has two reasons to delete a row: its file is gone, or --
once an ACL map governs the tree -- no rule grants the file any more. Only the
first was ever exercised (tests/test_ingest_v2.py calls it without `acl=`), so
the branch that makes a revoked rule actually revoke access ran in no test. A
regression there leaves a stale row from an earlier, broader policy readable.

The connection is the only fake: it answers the corpus-tier SELECT with the
rows given and records each DELETE. The ACL map and the disk are real. A live
counterpart runs against PostgreSQL in tests/integration/test_postgres_rls.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from scout.ingest import DocumentAclMap, reconcile_deletions


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.deleted: list[object] = []

    async def fetch(self, _sql: str, *_args: object) -> list[dict[str, object]]:
        return self._rows

    async def execute(self, sql: str, *args: object) -> str:
        assert sql.strip().startswith("DELETE FROM rag_documents")
        self.deleted.append(args[0])
        return "DELETE 1"

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, DocumentAclMap, dict[str, uuid.UUID]]:
    raw = tmp_path / "raw"
    (raw / "kept").mkdir(parents=True)
    (raw / "revoked").mkdir()
    (raw / "kept" / "a.md").write_text("# kept\n", encoding="utf-8")
    (raw / "revoked" / "b.md").write_text("# revoked\n", encoding="utf-8")
    acl_file = raw / ".acl.yaml"
    acl_file.write_text(
        'version: 1\nrules:\n  - path: "kept/**"\n    departments: [infra]\n',
        encoding="utf-8",
    )
    ids = {
        "raw/kept/a.md": uuid.uuid4(),
        "raw/revoked/b.md": uuid.uuid4(),
        "raw/gone.md": uuid.uuid4(),
    }
    return raw, DocumentAclMap.from_file(acl_file), ids


def _rows(ids: dict[str, uuid.UUID]) -> list[dict[str, object]]:
    return [{"source_uri": uri, "doc_id": doc_id} for uri, doc_id in ids.items()]


async def test_a_file_no_rule_grants_is_purged_though_it_is_on_disk(
    corpus: tuple[Path, DocumentAclMap, dict[str, uuid.UUID]],
) -> None:
    raw, acl, ids = corpus
    conn = _Connection(_rows(ids))

    deleted = await reconcile_deletions(raw, conn=conn, acl=acl)  # type: ignore[arg-type]

    assert sorted(deleted) == ["raw/gone.md", "raw/revoked/b.md"]
    assert set(conn.deleted) == {ids["raw/gone.md"], ids["raw/revoked/b.md"]}
    assert ids["raw/kept/a.md"] not in conn.deleted


async def test_without_a_map_only_missing_files_are_purged(
    corpus: tuple[Path, DocumentAclMap, dict[str, uuid.UUID]],
) -> None:
    """The unmapped rule needs a map; with none there is no policy to revoke."""
    raw, _acl, ids = corpus
    conn = _Connection(_rows(ids))

    deleted = await reconcile_deletions(raw, conn=conn)  # type: ignore[arg-type]

    assert deleted == ["raw/gone.md"]


async def test_dry_run_names_the_unmapped_row_and_deletes_nothing(
    corpus: tuple[Path, DocumentAclMap, dict[str, uuid.UUID]],
) -> None:
    raw, acl, ids = corpus
    conn = _Connection(_rows(ids))

    deleted = await reconcile_deletions(raw, conn=conn, acl=acl, dry_run=True)  # type: ignore[arg-type]

    assert "raw/revoked/b.md" in deleted
    assert conn.deleted == []
