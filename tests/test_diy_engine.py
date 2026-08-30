"""Contract tests for the V3 scoped wiki engine.

These replace the former cosine/SQLite/cache tests with guardrails for the
retrieval inversion: one shared pgvector backend for finding pages and direct
disk reads for canonical page content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from scout.chunker import LiteLLMBatchEmbedder
from scout.diy_engine import (
    Embedder,
    LiteLLMEmbedder,
    PageLike,
    ScoutDiyEngine,
    WikiHit,
    WikiPage,
)
from scout.types import RagChunk, Scope
from tests.fakes import FakeEmbedder


@dataclass(slots=True)
class RecordingBackend:
    chunks: Sequence[RagChunk] = ()
    calls: list[tuple[str, str | None, Scope | None, int]] = field(
        default_factory=list
    )

    async def retrieve(
        self,
        hint: str,
        *,
        path: str | None = None,
        scope: Scope | None = None,
        k: int = 10,
    ) -> Sequence[RagChunk]:
        self.calls.append((hint, path, scope, k))
        return self.chunks


@dataclass(slots=True)
class SyntheticPage:
    slug: str
    rel: str
    frontmatter: Mapping[str, object]
    body: str


@pytest.fixture
def scope() -> Scope:
    return Scope(departments=frozenset({"infra", "ai_eng"}))


def _chunk(
    *,
    text: str = "Best matching body passage",
    path: str = "concepts/page.md",
    score: float = 0.75,
    **meta: str,
) -> RagChunk:
    defaults = {
        "content_hash": "sha:page",
        "title": "Page Title",
        "type": "concept",
        "tldr": "A compact routing sentence.",
        "degraded": "false",
    }
    return RagChunk(
        text=text,
        file_path=path,
        score=score,
        meta={**defaults, **meta},
    )


def _engine(backend: RecordingBackend) -> ScoutDiyEngine:
    return ScoutDiyEngine(embedder=FakeEmbedder(), rag_backend=backend)


def _write_page(
    root: Path,
    *,
    rel: str = "concepts/page.md",
    title: str = "Page Title",
    body: str | None = None,
    page_type: str = "concept",
) -> Path:
    page = root / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"""---
title: {title}
type: {page_type}
updated: 2026-08-28
sources:
  - path: raw/report.pdf
    loc: p.2
    hint: exact evidence phrase
---
{body or '''# Page Title

## TL;DR
A compact routing sentence.

## Detail
Body evidence is read from the current Markdown file.

## Cross-References
See [[other|Other Page]] and [[third#Part]].
'''}""",
        encoding="utf-8",
    )
    return page


def _vault_engine(root: Path) -> ScoutDiyEngine:
    return ScoutDiyEngine.from_vault(
        FakeEmbedder(),
        wiki_dir=root,
        rag_backend=RecordingBackend(),
    )


def test_embedding_contract_is_the_shared_async_contract() -> None:
    embedder = FakeEmbedder()
    assert isinstance(embedder, Embedder)
    assert LiteLLMEmbedder is LiteLLMBatchEmbedder


def test_synthetic_page_satisfies_pagelike_protocol() -> None:
    page = SyntheticPage("page", "concepts/page.md", {}, "body")
    assert isinstance(page, PageLike)


async def test_scope_less_search_is_refused_before_any_shortcut() -> None:
    engine = _engine(RecordingBackend())
    with pytest.raises(ValueError, match="authenticated scope"):
        await engine.wiki_search("query")
    with pytest.raises(ValueError, match="authenticated scope"):
        await engine.wiki_search("", k=0)


async def test_search_threads_exact_scope_and_request(scope: Scope) -> None:
    backend = RecordingBackend([_chunk()])
    hits = await _engine(backend).wiki_search("body meaning", k=7, scope=scope)
    assert backend.calls == [("body meaning", None, scope, 7)]
    assert len(hits) == 1


@pytest.mark.parametrize(("query", "k"), [("", 5), ("   ", 5), ("query", 0), ("q", -1)])
async def test_empty_or_nonpositive_search_skips_backend(
    query: str, k: int, scope: Scope
) -> None:
    backend = RecordingBackend([_chunk()])
    assert await _engine(backend).wiki_search(query, k=k, scope=scope) == []
    assert backend.calls == []


async def test_backend_chunk_is_adapted_to_page_hit(scope: Scope) -> None:
    backend = RecordingBackend([_chunk(score=0.42)])
    (hit,) = await _engine(backend).wiki_search("query", scope=scope)
    assert isinstance(hit, WikiHit)
    assert hit.page_id == "page"
    assert hit.path == "concepts/page.md"
    assert hit.type == "concept"
    assert hit.title == "Page Title"
    assert hit.score == pytest.approx(0.42)
    assert hit.snippet == "A compact routing sentence."


@pytest.mark.parametrize("word_count", [40, 41, 100])
async def test_search_snippet_never_exceeds_forty_words(
    word_count: int, scope: Scope
) -> None:
    text = " ".join(f"word-{index}" for index in range(word_count))
    backend = RecordingBackend([_chunk(text=text, tldr="")])
    (hit,) = await _engine(backend).wiki_search("query", scope=scope)
    assert len(hit.snippet.split()) == min(word_count, 40)
    assert len(hit.snippet.split()) <= 40


async def test_seen_hash_returns_only_canonical_stub(scope: Scope) -> None:
    backend = RecordingBackend([_chunk()])
    (hit,) = await _engine(backend).wiki_search(
        "query", seen=["sha:page"], scope=scope
    )
    assert hit.seen is True
    assert hit.snippet == ""
    assert hit.canonical() == {
        "path": "concepts/page.md",
        "title": "Page Title",
        "seen": True,
    }


async def test_unseen_canonical_hit_is_bounded_data_shape(scope: Scope) -> None:
    backend = RecordingBackend([_chunk()])
    (hit,) = await _engine(backend).wiki_search("query", scope=scope)
    assert hit.canonical() == {
        "path": "concepts/page.md",
        "type": "concept",
        "score": 0.75,
        "snippet": "A compact routing sentence.",
        "seen": False,
        "degraded": False,
    }


async def test_degradation_state_and_reason_survive_adapter(scope: Scope) -> None:
    backend = RecordingBackend(
        [_chunk(degraded="true", degraded_reason="embedding_timeout")]
    )
    (hit,) = await _engine(backend).wiki_search("query", scope=scope)
    assert hit.degraded is True
    assert hit.reason == "embedding_timeout"
    assert hit.canonical()["reason"] == "embedding_timeout"


async def test_source_chunk_routes_to_its_wiki_page(scope: Scope) -> None:
    backend = RecordingBackend(
        [_chunk(path="raw/report.pdf", wiki_path="concepts/compiled.md")]
    )
    (hit,) = await _engine(backend).wiki_search("query", scope=scope)
    assert hit.path == "concepts/compiled.md"
    assert hit.page_id == "compiled"


async def test_search_deduplicates_chunks_routed_to_same_page(scope: Scope) -> None:
    backend = RecordingBackend(
        [
            _chunk(path="concepts/page.md", score=0.9),
            _chunk(path="raw/report.pdf", score=0.8, wiki_path="concepts/page.md"),
            _chunk(path="concepts/other.md", score=0.7, title="Other"),
        ]
    )
    hits = await _engine(backend).wiki_search("query", k=5, scope=scope)
    assert [hit.path for hit in hits] == ["concepts/page.md", "concepts/other.md"]


async def test_backend_failure_is_not_hidden(scope: Scope) -> None:
    class BrokenBackend(RecordingBackend):
        async def retrieve(
            self,
            hint: str,
            *,
            path: str | None = None,
            scope: Scope | None = None,
            k: int = 10,
        ) -> Sequence[RagChunk]:
            del hint, path, scope, k
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await _engine(BrokenBackend()).wiki_search("query", scope=scope)


async def test_cache_compatibility_argument_never_writes_local_files(
    tmp_path: Path, scope: Scope
) -> None:
    cache = tmp_path / "retired" / "cache.json"
    engine = ScoutDiyEngine(
        embedder=FakeEmbedder(),
        rag_backend=RecordingBackend([_chunk()]),
        cache_path=cache,
    )
    await engine.wiki_search("query", scope=scope)
    assert not cache.exists()
    assert list(tmp_path.rglob("*")) == []


async def test_internal_backend_reuses_shared_embedder_and_closes(
    monkeypatch: pytest.MonkeyPatch, scope: Scope
) -> None:
    from scout.backends import pgvector

    created: list[object] = []
    closed: list[bool] = []

    class OwnedBackend(RecordingBackend):
        def __init__(self, *, embedder: object) -> None:
            super().__init__([_chunk()])
            created.append(embedder)

        async def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(pgvector, "PgVectorRlsBackend", OwnedBackend)
    embedder = FakeEmbedder()
    engine = ScoutDiyEngine(embedder=embedder)
    await engine.wiki_search("query", scope=scope)
    await engine.aclose()
    await engine.aclose()
    assert created == [embedder]
    assert closed == [True]


async def test_injected_backend_is_not_closed_by_engine(scope: Scope) -> None:
    class ExternalBackend(RecordingBackend):
        closed = False

        async def close(self) -> None:
            self.closed = True

    backend = ExternalBackend([_chunk()])
    async with _engine(backend) as engine:
        await engine.wiki_search("query", scope=scope)
    assert backend.closed is False


async def test_scope_less_read_is_refused(tmp_path: Path) -> None:
    _write_page(tmp_path)
    with pytest.raises(ValueError, match="authenticated scope"):
        await _vault_engine(tmp_path).wiki_read("concepts/page.md")


async def test_full_read_returns_canonical_disk_envelope(
    tmp_path: Path, scope: Scope
) -> None:
    page_path = _write_page(tmp_path)
    page = await _vault_engine(tmp_path).wiki_read(
        "concepts/page.md", scope=scope
    )
    assert isinstance(page, WikiPage)
    assert page.body == page_path.read_text(encoding="utf-8").split("---\n", 2)[2]
    assert page.path == "concepts/page.md"
    assert page.title == "Page Title"
    assert page.type == "concept"
    assert page.updated == "2026-08-28"
    assert page.tldr == "A compact routing sentence."
    assert page.links == ("other", "third")
    assert page.sources[0]["path"] == "raw/report.pdf"  # type: ignore[index]
    envelope = page.canonical()
    assert set(envelope) == {
        "path",
        "title",
        "type",
        "updated",
        "tldr",
        "outline",
        "sections",
        "sources",
        "links",
        "content_hash",
    }
    assert envelope["sections"] == {
        "TL;DR": "A compact routing sentence.",
        "Detail": "Body evidence is read from the current Markdown file.",
        "Cross-References": "See [[other|Other Page]] and [[third#Part]].",
    }


async def test_tldr_mode_returns_minimal_envelope(
    tmp_path: Path, scope: Scope
) -> None:
    _write_page(tmp_path)
    page = await _vault_engine(tmp_path).wiki_read(
        "page", mode="tldr", scope=scope
    )
    assert set(page.canonical()) == {
        "path",
        "title",
        "type",
        "tldr",
        "content_hash",
    }
    assert "Body evidence" not in str(page.canonical())


async def test_outline_mode_excludes_section_bodies(
    tmp_path: Path, scope: Scope
) -> None:
    _write_page(tmp_path)
    page = await _vault_engine(tmp_path).wiki_read(
        "Page Title", mode="outline", scope=scope
    )
    envelope = page.canonical()
    assert set(envelope) == {
        "path",
        "title",
        "type",
        "tldr",
        "content_hash",
        "outline",
    }
    outline = cast(list[Mapping[str, object]], envelope["outline"])
    headings = [item["heading"] for item in outline]
    assert headings == ["TL;DR", "Detail", "Cross-References"]
    assert "Body evidence" not in str(envelope)


async def test_section_read_is_case_insensitive_and_narrow(
    tmp_path: Path, scope: Scope
) -> None:
    _write_page(tmp_path)
    page = await _vault_engine(tmp_path).wiki_read(
        "concepts/page.md", section="detail", scope=scope
    )
    assert page.mode == "section"
    assert page.canonical()["sections"] == {
        "Detail": "Body evidence is read from the current Markdown file."
    }
    assert "Cross-References" not in str(page.canonical())


async def test_missing_or_ambiguous_section_is_refused(
    tmp_path: Path, scope: Scope
) -> None:
    _write_page(
        tmp_path,
        body="""# Page Title

## TL;DR
Summary.

## First
### Notes
First notes.

## Second
### Notes
Second notes.
""",
    )
    engine = _vault_engine(tmp_path)
    with pytest.raises(KeyError, match="ambiguous"):
        await engine.wiki_read("page", section="Notes", scope=scope)
    with pytest.raises(KeyError, match="no such wiki section"):
        await engine.wiki_read("page", section="Missing", scope=scope)


async def test_invalid_read_mode_is_refused(tmp_path: Path, scope: Scope) -> None:
    _write_page(tmp_path)
    with pytest.raises(ValueError, match="unsupported"):
        await _vault_engine(tmp_path).wiki_read("page", mode="everything", scope=scope)


@pytest.mark.parametrize("identifier", ["page", "Page Title", "concepts/page.md", "concepts\\page.md"])
async def test_read_resolves_slug_title_and_normalized_path(
    identifier: str, tmp_path: Path, scope: Scope
) -> None:
    _write_page(tmp_path)
    page = await _vault_engine(tmp_path).wiki_read(identifier, scope=scope)
    assert page.path == "concepts/page.md"


async def test_read_rejects_traversal_and_unknown_page(
    tmp_path: Path, scope: Scope
) -> None:
    _write_page(tmp_path)
    engine = _vault_engine(tmp_path)
    with pytest.raises(KeyError, match="escapes"):
        await engine.wiki_read("../outside.md", scope=scope)
    with pytest.raises(KeyError, match="no such"):
        await engine.wiki_read("missing", scope=scope)


async def test_ambiguous_title_is_refused(tmp_path: Path, scope: Scope) -> None:
    _write_page(tmp_path, rel="concepts/one.md", title="Same Title")
    _write_page(tmp_path, rel="entities/two.md", title="Same Title")
    with pytest.raises(KeyError, match="ambiguous"):
        await _vault_engine(tmp_path).wiki_read("Same Title", scope=scope)


async def test_each_read_observes_current_disk_bytes(
    tmp_path: Path, scope: Scope
) -> None:
    path = _write_page(tmp_path)
    engine = _vault_engine(tmp_path)
    before = await engine.wiki_read("page", scope=scope)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "A compact routing sentence.", "A freshly edited routing sentence."
        ),
        encoding="utf-8",
    )
    after = await engine.wiki_read("page", scope=scope)
    assert before.content_hash != after.content_hash
    assert before.body != after.body
    assert after.tldr == "A freshly edited routing sentence."


async def test_horizontal_rule_survives_section_parsing(
    tmp_path: Path, scope: Scope
) -> None:
    _write_page(
        tmp_path,
        body="""# Page Title

## TL;DR
Summary.

## Detail
Above the rule.

---

Below the rule.
""",
    )
    page = await _vault_engine(tmp_path).wiki_read(
        "page", section="Detail", scope=scope
    )
    assert page.sections["Detail"] == "Above the rule.\n\n---\n\nBelow the rule."


async def test_read_uses_body_tldr_chain_without_summary_frontmatter(
    tmp_path: Path, scope: Scope
) -> None:
    _write_page(
        tmp_path,
        body="""# Page Title

This lead paragraph is the routing text even without summary metadata.

## Detail
Deeper evidence.
""",
    )
    page = await _vault_engine(tmp_path).wiki_read("page", mode="tldr", scope=scope)
    assert page.tldr == (
        "This lead paragraph is the routing text even without summary metadata."
    )


async def test_wikilinks_are_normalized_and_deduplicated(
    tmp_path: Path, scope: Scope
) -> None:
    _write_page(
        tmp_path,
        body="""# Page Title

## TL;DR
Summary.

## Links
[[target|Alias]], [[target#Section]], and [[second]].
""",
    )
    page = await _vault_engine(tmp_path).wiki_read("page", scope=scope)
    assert page.links == ("target", "second")


async def test_non_list_sources_default_to_empty(
    tmp_path: Path, scope: Scope
) -> None:
    page_path = _write_page(tmp_path)
    page_path.write_text(
        page_path.read_text(encoding="utf-8").replace(
            "sources:\n  - path: raw/report.pdf\n    loc: p.2\n    hint: exact evidence phrase",
            "sources: malformed",
        ),
        encoding="utf-8",
    )
    page = await _vault_engine(tmp_path).wiki_read("page", scope=scope)
    assert page.sources == ()
