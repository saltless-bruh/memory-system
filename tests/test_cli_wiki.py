"""Tests for the wiki family: `snpmemory read` and `snpmemory search`.

Both answer from the checkout, so these tests build a small vault on disk rather
than standing up a server. That is the property under test as much as the
output: a compiled page is a file, and reading it must not need the stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scout.cli.commands.wiki import _resolve, read  # noqa: E402
from scout.cli.errors import CliError  # noqa: E402
from scout.cli.result import ExitCode  # noqa: E402

PAGE = """---
type: concept
title: Convolutional Neural Networks
summary: A family of models for grid-like data.
entities: [CNN, pooling]
department: ai_eng
sources:
  - path: raw/papers/x.pdf
    hint: convolution kernels slide over the input
    loc: p.17
last_compiled: 2026-08-21
---

## TL;DR

A CNN shares weights across positions. See [[natural-language-processing]].
"""

OTHER = PAGE.replace("Convolutional Neural Networks", "Natural Language Processing")


def _vault(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki" / "concepts"
    wiki.mkdir(parents=True)
    (wiki / "convolutional-neural-networks.md").write_text(PAGE, encoding="utf-8")
    (wiki / "natural-language-processing.md").write_text(OTHER, encoding="utf-8")
    return tmp_path / "wiki"


def _pages(tmp_path: Path) -> list:
    from scout import vault

    return vault.load_pages(_vault(tmp_path))


def test_resolves_by_slug(tmp_path: Path) -> None:
    page = _resolve(_pages(tmp_path), "convolutional-neural-networks")
    assert page.title == "Convolutional Neural Networks"


def test_resolves_by_title_case_insensitively(tmp_path: Path) -> None:
    page = _resolve(_pages(tmp_path), "convolutional neural networks")
    assert page.slug == "convolutional-neural-networks"


def test_resolves_by_path(tmp_path: Path) -> None:
    pages = _pages(tmp_path)
    page = _resolve(pages, pages[0].path.as_posix())
    assert page.slug == pages[0].slug


def test_an_unknown_page_is_a_caller_mistake(tmp_path: Path) -> None:
    with pytest.raises(CliError) as caught:
        _resolve(_pages(tmp_path), "no-such-page")
    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_an_ambiguous_title_refuses_rather_than_choosing(tmp_path: Path) -> None:
    """Two pages can share a title across categories.

    Picking one for the caller is how the wrong page ends up cited, so the
    command names both and stops.
    """
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


def test_an_empty_identifier_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CliError):
        _resolve(_pages(tmp_path), "   ")


def test_read_puts_the_page_on_stdout_and_the_header_on_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`snpmemory read x > page.md` must write the page and nothing else."""
    from scout import vault
    from scout.cli.config import Config
    from scout.cli.registry import Prerequisite

    wiki = _vault(tmp_path)
    monkeypatch.setattr(vault, "WIKI_DIR", wiki)
    cfg = Config(prerequisite=Prerequisite.LOCAL, repo_root=tmp_path)

    result = read("convolutional-neural-networks", config=cfg)
    assert result.summary.startswith("---") or "TL;DR" in result.summary
    assert result.messages == (
        "Convolutional Neural Networks — " + result.data["path"],
    )
    assert result.data["wikilinks"] == ["natural-language-processing"]
    assert result.data["sources"][0]["loc"] == "p.17"


# ── search ────────────────────────────────────────────────────────────────


class _StubEmbedder:
    """A deterministic stand-in for the LiteLLM route.

    Embeds on a single axis — how many query terms a text contains — which is
    enough to make ranking observable without a gateway, and keeps the test
    offline as `pytest-socket` requires.
    """

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.terms = ("convolution", "kernel")

    async def __aenter__(self) -> _StubEmbedder:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(sum(term in text.casefold() for term in self.terms)), 1.0]
            for text in texts
        ]


def _config(tmp_path: Path):
    from scout.cli.config import Config
    from scout.cli.registry import Prerequisite

    return Config(
        prerequisite=Prerequisite.LOCAL,
        values={
            "LITELLM_MASTER_KEY": "test-key",
            "LITELLM_BASE_URL": "http://localhost:4000/v1",
        },
        repo_root=tmp_path,
    )


def test_search_ranks_the_matching_page_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout import diy_engine, vault
    from scout.cli.commands.wiki import search

    monkeypatch.setattr(vault, "WIKI_DIR", _vault(tmp_path))
    monkeypatch.setattr(diy_engine, "LiteLLMEmbedder", _StubEmbedder)
    monkeypatch.chdir(tmp_path)

    result = search("convolution", limit=2, config=_config(tmp_path))
    assert result.data["count"] >= 1
    assert result.data["hits"][0]["page_id"] == "convolutional-neural-networks"


def test_search_respects_the_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scout import diy_engine, vault
    from scout.cli.commands.wiki import search

    monkeypatch.setattr(vault, "WIKI_DIR", _vault(tmp_path))
    monkeypatch.setattr(diy_engine, "LiteLLMEmbedder", _StubEmbedder)
    monkeypatch.chdir(tmp_path)

    result = search("convolution", limit=1, config=_config(tmp_path))
    assert len(result.data["hits"]) == 1


def test_a_nonpositive_limit_is_rejected(tmp_path: Path) -> None:
    from scout.cli.commands.wiki import search

    with pytest.raises(CliError) as caught:
        search("anything", limit=0, config=_config(tmp_path))
    assert caught.value.to_result().exit_code == ExitCode.INPUT_VALIDATION


def test_an_unreachable_gateway_is_infrastructure_not_a_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 2, so nothing downstream treats a dead gateway as "no results"."""
    from scout import diy_engine, vault
    from scout.cli.commands.wiki import search

    class _Dead(_StubEmbedder):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise ConnectionError("gateway down")

    monkeypatch.setattr(vault, "WIKI_DIR", _vault(tmp_path))
    monkeypatch.setattr(diy_engine, "LiteLLMEmbedder", _Dead)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(CliError) as caught:
        search("convolution", limit=2, config=_config(tmp_path))
    result = caught.value.to_result()
    assert result.exit_code == ExitCode.INFRASTRUCTURE
    assert result.error is not None and result.error.retryable


def test_the_base_url_v1_suffix_is_not_doubled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The configured value is the OpenAI-compatible root and ends in /v1.

    The embedder posts to `/v1/embeddings` relative to its base_url, so passing
    the configured value through unchanged asks for `/v1/v1/embeddings`.
    """
    from scout import diy_engine, vault
    from scout.cli.commands.wiki import search

    seen: dict[str, object] = {}

    class _Recording(_StubEmbedder):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__()
            seen.update(kwargs)

    monkeypatch.setattr(vault, "WIKI_DIR", _vault(tmp_path))
    monkeypatch.setattr(diy_engine, "LiteLLMEmbedder", _Recording)
    monkeypatch.chdir(tmp_path)

    search("convolution", limit=1, config=_config(tmp_path))
    assert seen["base_url"] == "http://localhost:4000"
