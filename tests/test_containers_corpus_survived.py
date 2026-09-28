"""`corpus-survived` must tell rows lost from rows added.

The gate exists so a rebuild that destroys rows is visible. It compared the
live counts to the baseline with `==`, so an owner adding a page, or a probe
landing, turned it red exactly as a lost table would, and it stayed red until
someone hand-edited the JSON. These tests run the real group with only the
Postgres container replaced by a fake that answers the three count queries.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "v3" / "checks"))

import containers  # noqa: E402

BASELINE = {"documents": 441, "chunks": 2103, "null_embeddings": 0}


class _Postgres:
    def __init__(self, documents: int, chunks: int, nulls: int) -> None:
        self._answers = {
            "SELECT COUNT(*) FROM rag_documents;": documents,
            "SELECT COUNT(*) FROM rag_chunks;": chunks,
            "SELECT COUNT(*) FROM rag_chunks WHERE embedding IS NULL;": nulls,
        }

    def exec_run(self, argv: list[str]) -> tuple[int, bytes]:
        return 0, f" {self._answers[argv[-1]]}\n".encode()


def _live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    documents: int,
    chunks: int,
    nulls: int = 0,
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(BASELINE), encoding="utf-8")
    monkeypatch.setattr(containers, "BASELINE_FILE", baseline)

    postgres = _Postgres(documents, chunks, nulls)
    client = types.SimpleNamespace(
        containers=types.SimpleNamespace(get=lambda name: postgres)
    )
    fake_docker = types.ModuleType("docker")
    fake_docker.from_env = lambda: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", fake_docker)


def test_an_unchanged_corpus_survives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _live(monkeypatch, tmp_path, documents=441, chunks=2103)
    assert containers.group_corpus_survived() == "CORPUS SURVIVED VERIFIED"


def test_growth_is_not_loss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One page added since the snapshot: 442 documents, 2106 chunks."""
    _live(monkeypatch, tmp_path, documents=442, chunks=2106)
    assert containers.group_corpus_survived() == "CORPUS SURVIVED VERIFIED"


@pytest.mark.parametrize(
    ("documents", "chunks", "lost"),
    [(440, 2103, "document"), (441, 2000, "chunk"), (0, 0, "document")],
)
def test_loss_is_red(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    documents: int,
    chunks: int,
    lost: str,
) -> None:
    _live(monkeypatch, tmp_path, documents=documents, chunks=chunks)
    with pytest.raises(containers.GateFailure, match=lost):
        containers.group_corpus_survived()


def test_a_null_embedding_is_red_even_with_growth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _live(monkeypatch, tmp_path, documents=450, chunks=2200, nulls=3)
    with pytest.raises(containers.GateFailure, match="null embedding"):
        containers.group_corpus_survived()
