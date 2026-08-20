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
