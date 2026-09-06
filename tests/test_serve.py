"""Production Scout backend selection tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scout.serve import _build_production_backend


def test_production_server_rejects_fake_backend() -> None:
    with pytest.raises(ValueError, match="pgvector"):
        _build_production_backend("fake")


def test_production_server_builds_pgvector_only() -> None:
    sentinel = object()
    with patch("scout.backends.pgvector.PgVectorRlsBackend", return_value=sentinel):
        assert _build_production_backend("pgvector") is sentinel


def test_production_server_serves_only_the_wiki_tier() -> None:
    """The served surface is `wiki_search` and `wiki_read` and nothing else, so
    the backend behind it must be built on the wiki corpus.

    Asserting the keyword rather than just the class is what stops the tier
    from being deleted here unnoticed: `PgVectorRlsBackend()` defaults to every
    corpus, which is correct for the compile pipeline and wrong for the
    authenticated surface the demo talks to.
    """
    with patch("scout.backends.pgvector.PgVectorRlsBackend") as constructed:
        _build_production_backend("pgvector")
    assert constructed.call_args.kwargs.get("corpus") == "wiki"


# ── the startup guard: one vector space, or refuse to serve ────────────────


class _Census:
    """A connection that answers only the model census."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.queries.append(query)
        return self.rows


@pytest.mark.asyncio
async def test_startup_guard_rejects_a_mixed_index() -> None:
    """Two vector spaces in one index is F-2; it must be fatal, not silent."""
    from scout.serve import assert_single_embedding_model

    conn = _Census(
        [{"model": "snp-embed", "n": 2176}, {"model": None, "n": 127}]
    )
    with pytest.raises(RuntimeError, match="unstamped"):
        await assert_single_embedding_model(conn, expected_model="snp-embed")


@pytest.mark.asyncio
async def test_startup_guard_rejects_a_model_the_process_cannot_query_with() -> None:
    """An index built by another model returns near-random order, with no error."""
    from scout.serve import assert_single_embedding_model

    conn = _Census([{"model": "some/other-model", "n": 2303}])
    with pytest.raises(RuntimeError, match="some/other-model"):
        await assert_single_embedding_model(conn, expected_model="snp-embed")


@pytest.mark.asyncio
async def test_startup_guard_rejects_two_stamped_models() -> None:
    from scout.serve import assert_single_embedding_model

    conn = _Census(
        [{"model": "snp-embed", "n": 2000}, {"model": "fastembed/bge", "n": 300}]
    )
    with pytest.raises(RuntimeError, match="fastembed/bge"):
        await assert_single_embedding_model(conn, expected_model="snp-embed")


@pytest.mark.asyncio
async def test_startup_guard_refuses_an_empty_index() -> None:
    """A census that sees nothing has not observed its subject.

    Serving on an empty census would let the guard pass in exactly the state it
    cannot distinguish from fail-closed RLS reading zero rows.
    """
    from scout.serve import assert_single_embedding_model

    with pytest.raises(RuntimeError, match="no chunks"):
        await assert_single_embedding_model(_Census([]), expected_model="snp-embed")


@pytest.mark.asyncio
async def test_startup_guard_accepts_a_single_matching_model() -> None:
    from scout.serve import assert_single_embedding_model

    conn = _Census([{"model": "snp-embed", "n": 2101}])
    await assert_single_embedding_model(conn, expected_model="snp-embed")


def test_the_guard_expects_what_the_served_backend_actually_embeds_with() -> None:
    """The guard compares the index against the *query* route.

    Reading `LITELLM_EMBED_MODEL` here instead -- the variable that configures
    the gateway -- would refuse to start against a correctly built index, because
    the backend's own embedder never uses it.
    """
    from scout.backends.pgvector import PgVectorRlsBackend
    from scout.chunker import configured_embedding_model
    from scout.serve import _expected_embedding_model

    backend = PgVectorRlsBackend(corpus="wiki")
    assert _expected_embedding_model() == configured_embedding_model()
    assert _expected_embedding_model() == backend.embedder.model
