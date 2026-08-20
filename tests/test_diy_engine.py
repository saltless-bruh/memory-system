"""Tests for scout.diy_engine — the Scout-DIY wiki engine fallback (T-2.4)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from scout.chunker import EmbeddingError
from scout.diy_engine import (
    LiteLLMEmbedder,
    PageLike,
    ScoutDiyEngine,
    WikiHit,
    WikiPage,
    cosine_similarity,
)
from tests.fakes import FakeEmbedder


@dataclass
class _SyntheticPage:
    """A minimal `PageLike` stand-in — no filesystem, no real vault."""

    slug: str
    rel: str
    frontmatter: Mapping[str, object]
    body: str = ""


def _page(slug: str, summary: str, body: str = "", **extra: object) -> _SyntheticPage:
    fm: dict[str, object] = {"summary": summary, **extra}
    return _SyntheticPage(slug=slug, rel=f"wiki/{slug}.md", frontmatter=fm, body=body)


@pytest.fixture
def pages() -> list[_SyntheticPage]:
    """Three pages: two clearly distinct topics + a third distinguishing tokens."""
    return [
        _page(
            "kerberoasting",
            "Kerberoasting requests a TGS for an SPN and cracks it offline",
            body="# Kerberoasting\n\nAttack technique body.",
        ),
        _page(
            "esc8",
            "ESC8 relays NTLM to the AD CS web enrollment endpoint",
            body="# ESC8\n\nADCS relay technique body.",
        ),
        _page(
            "phishing",
            "Phishing lures a user into opening a malicious attachment",
            body="# Phishing\n\nSocial engineering body.",
        ),
    ]


@pytest.fixture
def engine(pages: list[_SyntheticPage], tmp_path: Path) -> Iterator[ScoutDiyEngine]:
    instance = ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=pages, cache_path=tmp_path / "vector_cache.json"
    )
    yield instance
    instance.close()


# ── cosine_similarity (pure) ────────────────────────────────────────────
def test_cosine_similarity_identical_vectors() -> None:
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector_guard() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


def test_cosine_similarity_empty_vector_guard() -> None:
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0


def test_cosine_similarity_mismatched_length_guard() -> None:
    assert cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


# ── FakeEmbedder (pure/deterministic) ───────────────────────────────────
async def test_fake_embedder_deterministic() -> None:
    emb = FakeEmbedder()
    a = await emb.embed(["hello world"])
    b = await emb.embed(["hello world"])
    assert a == b


async def test_fake_embedder_empty_text_is_zero_vector() -> None:
    emb = FakeEmbedder(dims=8)
    (vec,) = await emb.embed([""])
    assert vec == [0.0] * 8


async def test_fake_embedder_preserves_order() -> None:
    emb = FakeEmbedder()
    out = await emb.embed(["alpha", "beta", "gamma"])
    assert len(out) == 3


# ── wiki_search ──────────────────────────────────────────────────────────
async def test_wiki_search_ranks_closest_page_first(engine: ScoutDiyEngine) -> None:
    hits = await engine.wiki_search("NTLM relay AD CS enrollment", k=5)
    assert hits[0].page_id == "esc8"


async def test_wiki_search_second_topic_ranks_first_for_its_query(
    engine: ScoutDiyEngine,
) -> None:
    hits = await engine.wiki_search("TGS SPN Kerberoasting offline crack", k=5)
    assert hits[0].page_id == "kerberoasting"


async def test_wiki_search_respects_k(engine: ScoutDiyEngine) -> None:
    hits = await engine.wiki_search("attack technique", k=2)
    assert len(hits) == 2


async def test_wiki_search_k_larger_than_corpus_returns_all(
    engine: ScoutDiyEngine,
) -> None:
    hits = await engine.wiki_search("phishing attachment", k=100)
    assert len(hits) == 3


async def test_wiki_search_zero_k_returns_empty(engine: ScoutDiyEngine) -> None:
    assert await engine.wiki_search("anything", k=0) == []


async def test_wiki_search_negative_k_returns_empty(engine: ScoutDiyEngine) -> None:
    assert await engine.wiki_search("anything", k=-1) == []


async def test_wiki_search_empty_query_does_not_raise(engine: ScoutDiyEngine) -> None:
    hits = await engine.wiki_search("", k=5)
    assert len(hits) == 3
    assert all(h.score > 0.0 for h in hits)


async def test_wiki_search_empty_corpus_returns_empty(tmp_path: Path) -> None:
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[], cache_path=tmp_path / "cache.json"
    ) as instance:
        assert await instance.wiki_search("anything") == []


async def test_wiki_search_returns_wikihit_shape(engine: ScoutDiyEngine) -> None:
    (hit,) = await engine.wiki_search("phishing attachment", k=1)
    assert isinstance(hit, WikiHit)
    assert hit.page_id == "phishing"
    assert hit.path == "wiki/phishing.md"
    assert hit.summary.startswith("Phishing lures")
    assert isinstance(hit.score, float)


async def test_wiki_search_accepts_callable_page_supplier(tmp_path: Path) -> None:
    calls = {"n": 0}

    def load() -> list[_SyntheticPage]:
        calls["n"] += 1
        return [_page("only", "the only page here")]

    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=load, cache_path=tmp_path / "cache.json"
    ) as instance:
        await instance.wiki_search("only page", k=5)
        await instance.wiki_search("only page again", k=5)
    # Index is built once and cached across calls.
    assert calls["n"] == 1


# ── wiki_read ────────────────────────────────────────────────────────────
async def test_wiki_read_by_path_returns_frontmatter_and_body(
    engine: ScoutDiyEngine,
) -> None:
    page = await engine.wiki_read("wiki/esc8.md")
    assert isinstance(page, WikiPage)
    assert page.frontmatter["summary"] == (
        "ESC8 relays NTLM to the AD CS web enrollment endpoint"
    )
    assert page.body == "# ESC8\n\nADCS relay technique body."


async def test_wiki_read_by_page_id(engine: ScoutDiyEngine) -> None:
    page = await engine.wiki_read("kerberoasting")
    assert str(page.frontmatter["summary"]).startswith("Kerberoasting")


async def test_wiki_read_normalizes_backslash_paths(engine: ScoutDiyEngine) -> None:
    page = await engine.wiki_read("wiki\\esc8.md")
    assert page.body == "# ESC8\n\nADCS relay technique body."


async def test_wiki_read_unknown_path_raises_keyerror(engine: ScoutDiyEngine) -> None:
    with pytest.raises(KeyError):
        await engine.wiki_read("wiki/does-not-exist.md")


async def test_wiki_read_extra_frontmatter_fields_pass_through(tmp_path: Path) -> None:
    p = _page("x", "summary text", department="redteam", entities=["foo"])
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[p], cache_path=tmp_path / "cache.json"
    ) as instance:
        page = await instance.wiki_read("x")
    assert page.frontmatter["department"] == "redteam"
    assert page.frontmatter["entities"] == ["foo"]


# ── PageLike structural typing ────────────────────────────────────────────
def test_synthetic_page_satisfies_pagelike_protocol() -> None:
    p = _page("s", "a summary")
    assert isinstance(p, PageLike)


# ── LiteLLMEmbedder (network mocked — never touches the real network) ────
class _TrackingTransport(httpx.AsyncBaseTransport):
    def __init__(self, payload: object | None = None, status_code: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.closed = False
        self.payload = payload or {
            "data": [
                {"index": 1, "embedding": [0.2, 0.3]},
                {"index": 0, "embedding": [0.1, 0.1]},
            ]
        }
        self.status_code = status_code

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            self.status_code,
            request=request,
            json=self.payload,
        )

    async def aclose(self) -> None:
        self.closed = True


class _RawJsonTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=(
                b'{"data":['
                b'{"index":0,"embedding":[0.1,1e999]},'
                b'{"index":1,"embedding":[0.3,0.4]}]}'
            ),
            headers={"content-type": "application/json"},
        )


async def test_litellm_embedder_builds_request_and_parses_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_threaded(*args: object, **kwargs: object) -> object:
        raise AssertionError("embedding HTTP must not use asyncio.to_thread")

    monkeypatch.setattr(asyncio, "to_thread", fail_if_threaded)
    transport = _TrackingTransport()
    emb = LiteLLMEmbedder(
        base_url="http://fake-litellm:4000",
        model="snp-embed",
        api_key="test-only-token",
        dim=2,
        transport=transport,
    )
    vectors = await emb.embed(["first", "second"])

    assert vectors == [[0.1, 0.1], [0.2, 0.3]]
    assert transport.closed is True
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == "http://fake-litellm:4000/v1/embeddings"
    assert request.headers["authorization"] == "Bearer test-only-token"
    assert request.read() == b'{"model":"snp-embed","input":["first","second"]}'


async def test_litellm_embedder_context_reuses_then_closes_transport() -> None:
    transport = _TrackingTransport()
    embedder = LiteLLMEmbedder(
        api_key="test-only-token", dim=2, transport=transport
    )

    async with embedder:
        await embedder.embed(["first", "second"])
        await embedder.embed(["first", "second"])
        assert transport.closed is False

    assert transport.closed is True
    await embedder.aclose()  # idempotent
    await embedder.close()  # compatibility hook is also idempotent


def test_litellm_embedder_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)

    with pytest.raises(ValueError, match="LITELLM_MASTER_KEY"):
        LiteLLMEmbedder()


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"data": [{"index": 0, "embedding": [0.1, 0.2]}]}, "cardinality"),
        (
            {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2]},
                    {"index": 0, "embedding": [0.3, 0.4]},
                ]
            },
            "duplicate index",
        ),
        (
            {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2]},
                    {"index": 2, "embedding": [0.3, 0.4]},
                ]
            },
            "indices",
        ),
        (
            {
                "data": [
                    {"index": 0, "embedding": [0.1, "bad"]},
                    {"index": 1, "embedding": [0.3, 0.4]},
                ]
            },
            "non-numeric",
        ),
        (
            {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2]},
                    {"index": 1, "embedding": [0.3]},
                ]
            },
            "dimension",
        ),
    ],
)
async def test_litellm_embedder_rejects_malformed_response_contract(
    payload: object, match: str
) -> None:
    embedder = LiteLLMEmbedder(
        api_key="test-only-token",
        dim=2,
        transport=_TrackingTransport(payload),
    )

    with pytest.raises(EmbeddingError, match=match):
        await embedder.embed(["first", "second"])


async def test_litellm_embedder_rejects_non_finite_response_values() -> None:
    embedder = LiteLLMEmbedder(
        api_key="test-only-token",
        dim=2,
        transport=_RawJsonTransport(),
    )

    with pytest.raises(EmbeddingError, match="non-finite"):
        await embedder.embed(["first", "second"])


async def test_litellm_embedder_normalizes_http_failures() -> None:
    embedder = LiteLLMEmbedder(
        api_key="test-only-token",
        dim=2,
        transport=_TrackingTransport({"error": "synthetic"}, status_code=503),
    )

    with pytest.raises(EmbeddingError, match="call failed"):
        await embedder.embed(["first"])


# ── from_vault (real integration against synthetic vault files on disk) ──
async def test_from_vault_real_integration_with_synthetic_vault(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    techniques_dir = wiki_dir / "techniques"
    techniques_dir.mkdir(parents=True)

    page_file = techniques_dir / "kerberoasting.md"
    page_file.write_text(
        """---
type: technique
title: Kerberoasting Attack
summary: Kerberoasting requests a service ticket for an SPN to crack offline.
entities: [kerberoasting, active-directory, spn]
department: redteam
sources: []
last_compiled: 2026-08-18
---
## TL;DR
Verbatim attack notes.
""",
        encoding="utf-8",
    )

    with ScoutDiyEngine.from_vault(
        FakeEmbedder(),
        wiki_dir=wiki_dir,
        cache_path=tmp_path / "cache.json",
    ) as instance:
        hits = await instance.wiki_search("service ticket SPN crack", k=5)
        assert len(hits) == 1
        assert hits[0].page_id == "kerberoasting"
        assert "ticket for an SPN" in hits[0].summary

        read_page = await instance.wiki_read("kerberoasting")
        assert read_page.frontmatter["title"] == "Kerberoasting Attack"
        assert "Verbatim attack notes." in read_page.body


async def test_ensure_index_creates_and_populates_fts(tmp_path: Path) -> None:
    cache_path = tmp_path / "vector_cache.json"
    p1 = _page("p1", "some summary text")
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[p1], cache_path=cache_path
    ) as instance:
        await instance._ensure_index()

    search_db = cache_path.parent / "search.db"
    assert search_db.exists()

    import sqlite3

    with sqlite3.connect(search_db) as conn:
        cursor = conn.execute("SELECT page_id, summary FROM fts_index")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0] == ("p1", "some summary text")


async def test_ensure_index_rebuilds_fts_when_search_db_missing(tmp_path: Path) -> None:
    """When vector cache exists but search.db is deleted/missing, FTS index is rebuilt."""
    import sqlite3

    cache_path = tmp_path / "vector_cache.json"
    p1 = _page("p1", "summary for page one")
    p2 = _page("p2", "summary for page two")

    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[p1, p2], cache_path=cache_path
    ) as engine1:
        await engine1._ensure_index()

    search_db = cache_path.parent / "search.db"
    assert search_db.exists()
    assert cache_path.exists()

    # Simulate missing/corrupted search.db while cache_path remains
    search_db.unlink()
    assert not search_db.exists()

    # New engine instance with existing cache_path must rebuild search.db
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[p1, p2], cache_path=cache_path
    ) as engine2:
        await engine2._ensure_index()

    assert search_db.exists()
    with sqlite3.connect(search_db) as conn:
        cursor = conn.execute("SELECT page_id FROM fts_index ORDER BY page_id")
        rows = [r[0] for r in cursor.fetchall()]
        assert rows == ["p1", "p2"]


async def test_ensure_index_uses_cache_and_only_embeds_uncached(tmp_path: Path) -> None:
    cache_path = tmp_path / "vector_cache.json"

    calls = {"embed": 0}

    class CountingEmbedder(FakeEmbedder):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            calls["embed"] += 1
            return await super().embed(texts)

    p1 = _page("p1", "summary one", entities=["a", "b"])
    p2 = _page("p2", "summary two", entities=["c"])

    # First run: should embed the 2 pages + 1 query
    with ScoutDiyEngine(
        embedder=CountingEmbedder(), pages=[p1, p2], cache_path=cache_path
    ) as engine1:
        await engine1.wiki_search("query")
    assert calls["embed"] == 2

    # Second run with same pages: should read from cache, only embed query
    calls["embed"] = 0
    with ScoutDiyEngine(
        embedder=CountingEmbedder(), pages=[p1, p2], cache_path=cache_path
    ) as engine2:
        await engine2.wiki_search("query")
    assert calls["embed"] == 1

    # Third run with one new page: should embed the 1 new page + 1 query
    p3 = _page("p3", "summary three", entities=[])
    calls["embed"] = 0
    with ScoutDiyEngine(
        embedder=CountingEmbedder(), pages=[p1, p2, p3], cache_path=cache_path
    ) as engine3:
        await engine3.wiki_search("query")
    assert calls["embed"] == 2


def _fts_rows(search_db: Path) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(search_db) as connection:
        cursor = connection.execute(
            "SELECT page_id, title, summary, entities FROM fts_index ORDER BY page_id"
        )
        records: list[tuple[str, str, str, str]] = []
        for row in cursor.fetchall():
            page_id, title, summary, entities = row
            records.append((str(page_id), str(title), str(summary), str(entities)))
        return records


async def test_fts_reconciles_title_only_change_with_warm_vector_cache(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "vector_cache.json"
    first = _page("p1", "unchanged summary", title="Old title", entities=["alpha"])
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[first], cache_path=cache_path
    ) as engine1:
        await engine1._ensure_index()

    changed = _page("p1", "unchanged summary", title="New title", entities=["alpha"])
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[changed], cache_path=cache_path
    ) as engine2:
        await engine2._ensure_index()

    assert _fts_rows(tmp_path / "search.db") == [
        ("p1", "New title", "unchanged summary", "alpha")
    ]


async def test_fts_deletes_removed_pages_without_orphans(tmp_path: Path) -> None:
    cache_path = tmp_path / "vector_cache.json"
    p1 = _page("p1", "first", title="First", entities=[])
    p2 = _page("p2", "second", title="Second", entities=[])
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[p1, p2], cache_path=cache_path
    ) as engine1:
        await engine1._ensure_index()

    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[p2], cache_path=cache_path
    ) as engine2:
        await engine2._ensure_index()

    assert [row[0] for row in _fts_rows(tmp_path / "search.db")] == ["p2"]


async def test_fts_reconciles_summary_and_entity_changes(tmp_path: Path) -> None:
    cache_path = tmp_path / "vector_cache.json"
    before = _page("p1", "old summary", title="Title", entities=["old"])
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[before], cache_path=cache_path
    ) as engine1:
        await engine1._ensure_index()

    after = _page("p1", "new summary", title="Title", entities=["new", "beta"])
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[after], cache_path=cache_path
    ) as engine2:
        await engine2._ensure_index()

    assert _fts_rows(tmp_path / "search.db") == [
        ("p1", "Title", "new summary", "beta,new")
    ]


async def test_fts_repairs_stale_row_even_when_vector_cache_is_warm(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "vector_cache.json"
    page = _page("p1", "current summary", title="Current", entities=["entity"])
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[page], cache_path=cache_path
    ) as engine1:
        await engine1._ensure_index()

    search_db = tmp_path / "search.db"
    with sqlite3.connect(search_db) as connection:
        connection.execute(
            "UPDATE fts_index SET title = ?, summary = ? WHERE page_id = ?",
            ("Stale", "stale summary", "p1"),
        )
        connection.commit()

    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[page], cache_path=cache_path
    ) as engine2:
        await engine2._ensure_index()

    assert _fts_rows(search_db) == [("p1", "Current", "current summary", "entity")]


async def test_fts_rebuilds_empty_database_with_warm_vector_cache(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "vector_cache.json"
    page = _page("p1", "summary", title="Title", entities=["entity"])
    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[page], cache_path=cache_path
    ) as engine1:
        await engine1._ensure_index()

    search_db = tmp_path / "search.db"
    with sqlite3.connect(search_db) as connection:
        connection.execute("DELETE FROM fts_index")
        connection.commit()

    with ScoutDiyEngine(
        embedder=FakeEmbedder(), pages=[page], cache_path=cache_path
    ) as engine2:
        await engine2._ensure_index()

    assert _fts_rows(search_db) == [("p1", "Title", "summary", "entity")]


async def test_engine_context_closes_sqlite_and_close_is_idempotent(
    tmp_path: Path,
) -> None:
    engine = ScoutDiyEngine(
        embedder=FakeEmbedder(),
        pages=[_page("p1", "summary")],
        cache_path=tmp_path / "vector_cache.json",
    )

    with engine:
        await engine._ensure_index()
        assert engine._fts_conn is not None

    assert engine._fts_conn is None
    engine.close()
    engine.close()


async def test_engine_async_context_closes_sqlite(tmp_path: Path) -> None:
    engine = ScoutDiyEngine(
        embedder=FakeEmbedder(),
        pages=[_page("p1", "summary")],
        cache_path=tmp_path / "vector_cache.json",
    )

    async with engine as active:
        await active._ensure_index()
        assert active._fts_conn is not None

    assert engine._fts_conn is None


async def test_engine_repeated_open_close_cycles_leave_database_usable(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "vector_cache.json"
    page = _page("p1", "summary", title="Title")

    for _ in range(3):
        with ScoutDiyEngine(
            embedder=FakeEmbedder(), pages=[page], cache_path=cache_path
        ) as engine:
            await engine._ensure_index()

    assert _fts_rows(tmp_path / "search.db") == [("p1", "Title", "summary", "")]
