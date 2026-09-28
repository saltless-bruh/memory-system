"""`snpmemory ingest` must name a document the way sync-job names it.

A document's identity is its `source_uri`, relative to the parent of the corpus
root: `raw/papers/x.md`. The CLI used to hand `ingest_directory` the *target*
directory and let it name documents relative to that directory's parent, so
`--dir raw/papers` wrote `papers/x.md` -- a second row beside sync-job's, both
retrievable -- and `--path raw/papers/x.md` filtered its own result away and
reported "indexed 0" with exit 0.

These tests drive the real pipeline in dry-run mode, which parses and chunks
but needs no database. The suite's other ingest tests replace
`ingest_directory` with a spy that returns canonical rows, which is exactly why
none of them could see this.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import asyncpg
import pytest

from scout.cli.commands.ingest import ingest
from scout.cli.config import Config
from scout.cli.registry import Prerequisite
from scout.cli.result import ExitCode
from scout.ingest import reconcile_deletions

_ACL = """version: 1
rules:
  - path: "**"
    departments: [ai_eng]
"""


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path.resolve()
    raw = repo / "raw"
    (raw / "papers" / "deep").mkdir(parents=True)
    (raw / "papers" / "x.md").write_text("# X\n\nbody of x\n", encoding="utf-8")
    (raw / "papers" / "deep" / "y.md").write_text("# Y\n\nbody\n", encoding="utf-8")
    (raw / "top.md").write_text("# Top\n\nbody\n", encoding="utf-8")
    (raw / ".acl.yaml").write_text(_ACL, encoding="utf-8")
    return repo


def _config(repo: Path) -> Config:
    return Config(prerequisite=Prerequisite.LOCAL, values={}, repo_root=repo)


def _uris(data: object) -> list[str]:
    assert isinstance(data, dict)
    return sorted(str(row["source_uri"]) for row in data["documents"])


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("raw", ["raw/papers/deep/y.md", "raw/papers/x.md", "raw/top.md"]),
        ("raw/papers", ["raw/papers/deep/y.md", "raw/papers/x.md"]),
        ("raw/papers/deep", ["raw/papers/deep/y.md"]),
    ],
)
def test_a_directory_run_names_documents_from_the_corpus_root(
    tmp_path: Path, target: str, expected: list[str]
) -> None:
    repo = _repo(tmp_path)

    result = ingest(dir=target, dry_run=True, config=_config(repo))

    assert _uris(result.data) == expected
    assert result.data["indexed"] == len(expected)


@pytest.mark.parametrize(
    "target", ["raw/top.md", "raw/papers/x.md", "raw/papers/deep/y.md"]
)
def test_a_single_file_run_indexes_that_file_under_its_canonical_name(
    tmp_path: Path, target: str
) -> None:
    repo = _repo(tmp_path)

    result = ingest(path=target, dry_run=True, config=_config(repo))

    assert _uris(result.data) == [target]
    assert result.data["indexed"] == 1
    assert result.exit_code == ExitCode.SUCCESS


class _IndexedRows:
    """The untiered rows a previous sync-job run left, all canonically named."""

    def __init__(self, uris: list[str]) -> None:
        self.rows = [
            {"doc_id": index, "source_uri": uri} for index, uri in enumerate(uris)
        ]
        self.deleted: list[object] = []

    async def fetch(self, _sql: str, *_args: object) -> list[dict[str, object]]:
        return self.rows

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(self, _sql: str, *args: object) -> str:
        self.deleted.append(args[0])
        return "DELETE 1"


@pytest.mark.asyncio
async def test_reconciling_a_subtree_sweeps_that_subtree_by_its_canonical_name(
    tmp_path: Path,
) -> None:
    """A sweep of `raw/papers` looked for rows named `papers/...` and found none,
    so a file deleted from the subtree kept its rows."""
    repo = _repo(tmp_path)
    conn = _IndexedRows(
        [
            "raw/papers/x.md",
            "raw/papers/gone.md",
            "raw/elsewhere/gone.md",
            "raw/top.md",
        ]
    )

    deleted = await reconcile_deletions(
        dir_path=repo / "raw" / "papers",
        base_dir=repo,
        conn=cast(asyncpg.Connection, conn),
    )

    assert deleted == ["raw/papers/gone.md"]
    assert conn.deleted == [1]
