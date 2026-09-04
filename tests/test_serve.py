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
