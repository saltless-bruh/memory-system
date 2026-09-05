"""CLI parity tests for V3 ``wiki_search`` and ``wiki_read``."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scout.cli.commands.wiki import _resolve, read, search
from scout.cli.config import Config
from scout.cli.errors import CliError
from scout.cli.registry import Prerequisite
from scout.cli.result import ExitCode
from scout.diy_engine import ScoutDiyEngine
from scout.types import RagChunk, Scope
from tests.fakes import FakeEmbedder

PAGE = """---
type: concept
title: Convolutional Neural Networks
updated: 2026-08-31
sources:
  - path: raw/papers/x.pdf
    hint: convolution kernels slide over the input
    loc: p.17
---
# Convolutional Neural Networks

## TL;DR
A CNN shares weights across positions.

## Detail
Convolution kernels slide across grid-like data. See [[natural-language-processing]].
"""

OTHER = PAGE.replace(
    "Convolutional Neural Networks", "Natural Language Processing"
).replace("A CNN shares", "A language model shares")


def _vault(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki" / "concepts"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "convolutional-neural-networks.md").write_text(PAGE, encoding="utf-8")
    (wiki / "natural-language-processing.md").write_text(OTHER, encoding="utf-8")
    return tmp_path / "wiki"


def _pages(tmp_path: Path) -> list[Any]:
    from scout import vault

    return vault.load_pages(_vault(tmp_path))


def _config(tmp_path: Path, **values: str) -> Config:
    return Config(
        prerequisite=Prerequisite.LOCAL,
        values=values,
        repo_root=tmp_path,
    )


class RecordingBackend:
    def __init__(self, chunks: Sequence[RagChunk] = ()) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, Scope | None, int]] = []
        self.closed = False

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        del path
        self.calls.append((hint, scope, k))
        return self.chunks

    async def close(self) -> None:
        self.closed = True


def _chunk(**meta: str) -> RagChunk:
    return RagChunk(
        text="Convolution body section",
        file_path="concepts/convolutional-neural-networks.md",
        score=0.7,
        meta={
            "title": "Convolutional Neural Networks",
            "type": "concept",
            "tldr": "A CNN shares weights across positions.",
            "content_hash": "sha:cnn",
            "degraded": "false",
            **meta,
        },
    )


def _install_search_engine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backend: RecordingBackend,
) -> None:
    from scout import vault
    from scout.cli.commands import wiki as commands

    wiki_dir = _vault(tmp_path)
    monkeypatch.setattr(vault, "WIKI_DIR", wiki_dir)

    def build(_cfg: Config, selected_wiki: Path) -> ScoutDiyEngine:
        assert selected_wiki == wiki_dir
        return ScoutDiyEngine.from_vault(
            FakeEmbedder(),
            wiki_dir=wiki_dir,
            rag_backend=backend,
        )

    monkeypatch.setattr(commands, "_build_search_engine", build)


def test_resolves_by_slug(tmp_path: Path) -> None:
    page = _resolve(_pages(tmp_path), "convolutional-neural-networks")
    assert page.title == "Convolutional Neural Networks"


def test_resolves_by_title_case_insensitively(tmp_path: Path) -> None:
    page = _resolve(_pages(tmp_path), "convolutional neural networks")
    assert page.slug == "convolutional-neural-networks"


def test_resolves_by_path(tmp_path: Path) -> None:
    pages = _pages(tmp_path)
    page = _resolve(pages, pages[0].path.as_posix())
    assert page.slug == "convolutional-neural-networks"


def test_unknown_or_empty_page_is_input_error(tmp_path: Path) -> None:
    for identifier in ("no-such-page", "   "):
        with pytest.raises(CliError) as caught:
            _resolve(_pages(tmp_path), identifier)
        assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_ambiguous_title_refuses_instead_of_choosing(tmp_path: Path) -> None:
    from scout import vault

    wiki = _vault(tmp_path)
    duplicate = wiki / "guides"
    duplicate.mkdir()
    (duplicate / "cnn-guide.md").write_text(PAGE, encoding="utf-8")
    with pytest.raises(CliError) as caught:
        _resolve(vault.load_pages(wiki), "Convolutional Neural Networks")
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.INPUT_VALIDATION
    assert result.error is not None
    assert len(result.error.details["candidates"]) == 2


def test_read_returns_full_canonical_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout import vault

    monkeypatch.setattr(vault, "WIKI_DIR", _vault(tmp_path))
    result = read(
        "convolutional-neural-networks",
        dept="ai_eng",
        config=_config(tmp_path),
    )
    assert set(result.data) == {
        "path",
        "title",
        "type",
        "tldr",
        "content_hash",
        "updated",
        "outline",
        "sections",
        "sources",
        "links",
    }
    assert result.data["tldr"] == "A CNN shares weights across positions."
    assert result.data["links"] == ["natural-language-processing"]
    assert result.data["sources"][0]["loc"] == "p.17"
    assert "Convolution kernels" in result.summary


@pytest.mark.parametrize("mode", ["tldr", "outline"])
def test_read_modes_are_canonical_and_bounded(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout import vault

    monkeypatch.setattr(vault, "WIKI_DIR", _vault(tmp_path))
    result = read(
        "convolutional-neural-networks",
        dept="ai_eng",
        mode=mode,
        config=_config(tmp_path),
    )
    assert "sections" not in result.data
    assert "sources" not in result.data
    assert "Convolution kernels" not in str(result.data)


def test_read_one_section_returns_no_other_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout import vault

    monkeypatch.setattr(vault, "WIKI_DIR", _vault(tmp_path))
    result = read(
        "convolutional-neural-networks",
        dept="ai_eng",
        section="Detail",
        config=_config(tmp_path),
    )
    assert result.data["sections"] == {
        "Detail": (
            "Convolution kernels slide across grid-like data. "
            "See [[natural-language-processing]]."
        )
    }
    assert "A CNN shares" not in result.summary


@pytest.mark.parametrize("dept", ["all", "unknown", ""])
def test_read_rejects_noncanonical_local_scope(dept: str, tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        read("page", dept=dept, config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_search_returns_canonical_hits_and_threads_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = RecordingBackend([_chunk()])
    _install_search_engine(monkeypatch, tmp_path, backend)
    result = search(
        "convolution",
        dept="ai_eng",
        limit=2,
        config=_config(tmp_path),
    )
    assert result.data == {
        "query": "convolution",
        "count": 1,
        "hits": [
            {
                "path": "concepts/convolutional-neural-networks.md",
                "type": "concept",
                "score": 0.7,
                "snippet": "A CNN shares weights across positions.",
                "seen": False,
                "degraded": False,
            }
        ],
    }
    assert backend.calls == [
        ("convolution", Scope(departments=frozenset({"ai_eng"})), 2)
    ]
    assert backend.closed is True


def test_search_seen_hash_returns_stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = RecordingBackend([_chunk()])
    _install_search_engine(monkeypatch, tmp_path, backend)
    result = search(
        "convolution",
        dept="ai_eng",
        seen=["sha:cnn"],
        config=_config(tmp_path),
    )
    assert result.data["hits"] == [
        {
            "path": "concepts/convolutional-neural-networks.md",
            "title": "Convolutional Neural Networks",
            "seen": True,
        }
    ]


def test_search_rejects_nonpositive_limit_before_building(tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        search("anything", dept="infra", limit=0, config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_search_backend_failure_is_infrastructure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class DeadBackend(RecordingBackend):
        async def retrieve(
            self,
            hint: str,
            *,
            path: str | None = None,
            scope: Scope | None = None,
            k: int = 10,
        ) -> Sequence[RagChunk]:
            del hint, path, scope, k
            raise ConnectionError("database down")

    backend = DeadBackend()
    _install_search_engine(monkeypatch, tmp_path, backend)
    with pytest.raises(CliError) as caught:
        search("convolution", dept="ai_eng", config=_config(tmp_path))
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.INFRASTRUCTURE
    assert result.error is not None and result.error.retryable
    assert backend.closed is True


def test_search_configuration_failure_is_infrastructure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout.cli.commands import wiki as commands

    def broken(_cfg: Config, _wiki: Path) -> None:
        raise RuntimeError("bad configuration")

    monkeypatch.setattr(commands, "_build_search_engine", broken)
    with pytest.raises(CliError) as caught:
        search("query", dept="infra", config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.INFRASTRUCTURE


def test_shared_embedder_accepts_configured_v1_root_without_rewriting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout import chunker, config
    from scout.backends import pgvector
    from scout.cli.commands.wiki import _build_search_engine

    seen: dict[str, object] = {}

    class CapturingEmbedder(FakeEmbedder):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            seen.update(kwargs)

    class CapturingBackend(RecordingBackend):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            seen["backend"] = kwargs

    settings = SimpleNamespace(
        host="db",
        port=5432,
        database="rag",
        user="query",
        password="secret",
    )
    monkeypatch.setattr(config, "postgres_settings", lambda *_a, **_k: settings)
    monkeypatch.setattr(chunker, "LiteLLMBatchEmbedder", CapturingEmbedder)
    monkeypatch.setattr(pgvector, "PgVectorRlsBackend", CapturingBackend)

    engine = _build_search_engine(
        _config(
            tmp_path,
            LITELLM_BASE_URL="http://gateway:4000/v1",
            LITELLM_MASTER_KEY="token",
            LITELLM_EMBED_MODEL="pinned-model-001",
        ),
        _vault(tmp_path),
    )
    assert seen["base_url"] == "http://gateway:4000/v1"
    assert seen["model"] == "pinned-model-001"
    assert engine.rag_backend is not None


def test_cli_search_engine_is_built_on_the_wiki_tier(tmp_path: Path) -> None:
    """`snpmemory wiki search` answers from the same tier the served surface
    does. If the CLI were unfiltered it would surface raw evidence that
    `wiki_search` hides, and the two paths would disagree about what the vault
    contains.
    """
    from unittest.mock import patch

    from scout.cli.commands.wiki import _build_search_engine

    cfg = _config(
        tmp_path,
        POSTGRES_HOST="127.0.0.1",
        POSTGRES_PORT="5432",
        POSTGRES_DB="snp_rag",
        POSTGRES_QUERY_USER="rag_app_role",
        POSTGRES_QUERY_PASSWORD="unused-in-this-test",
    )
    with (
        patch("scout.backends.pgvector.PgVectorRlsBackend") as constructed,
        patch("scout.diy_engine.ScoutDiyEngine.from_vault"),
    ):
        _build_search_engine(cfg, _vault(tmp_path))
    assert constructed.call_args.kwargs.get("corpus") == "wiki"


def test_ingest_wiki_reports_per_page_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command must report what it indexed, not merely exit zero."""
    from scout.cli.commands.wiki import ingest_wiki_command

    async def fake_ingest_wiki(
        wiki_dir: Path, **kwargs: object
    ) -> list[dict[str, object]]:
        assert wiki_dir == Path("wiki")
        assert kwargs.get("dry_run") is True
        return [
            {
                "source_uri": "a.md",
                "title": "A",
                "chunks_count": 3,
                "status": "indexed",
            },
            {
                "source_uri": "b.md",
                "title": "B",
                "chunks_count": 0,
                "status": "skipped_no_body",
            },
        ]

    monkeypatch.setattr("scout.wiki_ingest.ingest_wiki", fake_ingest_wiki)
    result = ingest_wiki_command(dir="wiki", dry_run=True)

    assert result.data["pages"] == 2
    assert result.data["indexed"] == 1
    assert result.data["skipped"] == 1


def test_ingest_wiki_is_a_declared_shipped_command() -> None:
    """A command absent from the manifest is not a shipped surface."""
    source = Path("scout/cli/declarations.py").read_text()
    assert '"ingest-wiki"' in source
    assert "scout.cli.commands.wiki:ingest_wiki_command" in source
