"""Tests for the RAG-Anything HTTP backend (urllib mocked; no network)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from scout.backends.rag_anything_http import RagAnythingHttpBackend
from scout.core import post_filter, rag_fetch
from scout.types import Address, FetchStatus, RagBackend, Scope


def _fake_response(payload: dict[str, Any]) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


@pytest.fixture
def patched_urlopen(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch urllib.request.urlopen to return a canned /retrieve payload."""
    seen: dict[str, Any] = {}
    payload = {
        "hint": "kerberoasting",
        "chunks": [
            {
                "text": "svc-sql SPN cracked offline",
                "file_path": "raw/reports/acme.pdf",
                "reference_id": "1",
                "score": 0.0,
            },
            {
                "text": "ESC8 relay",
                "file_path": "raw/advisories/adcs.md",
                "reference_id": "2",
                "score": 0.0,
            },
        ],
    }

    class _CM:
        def __init__(self, data: io.BytesIO) -> None:
            self._data = data

        def __enter__(self) -> io.BytesIO:
            return self._data

        def __exit__(self, *a: object) -> None:
            return None

    def fake_urlopen(req: Any, timeout: float | None = None) -> _CM:
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        return _CM(_fake_response(payload))

    monkeypatch.setattr(
        "scout.backends.rag_anything_http.urllib.request.urlopen", fake_urlopen
    )
    return seen


def test_is_a_rag_backend() -> None:
    assert isinstance(RagAnythingHttpBackend(), RagBackend)


async def test_retrieve_maps_chunks(patched_urlopen: dict[str, Any]) -> None:
    backend = RagAnythingHttpBackend(base_url="http://rag:8000")
    out = await backend.retrieve("kerberoasting", k=5)
    assert [c.file_path for c in out] == [
        "raw/reports/acme.pdf",
        "raw/advisories/adcs.md",
    ]
    assert out[0].meta["reference_id"] == "1"
    # request went to the right endpoint with the hint + k
    assert patched_urlopen["url"].endswith("/retrieve")
    assert patched_urlopen["body"] == {"hint": "kerberoasting", "k": 5}


async def test_scope_accepted_but_not_sent(patched_urlopen: dict[str, Any]) -> None:
    backend = RagAnythingHttpBackend()
    await backend.retrieve("x", scope=Scope(team="redteam"), k=3)
    # scope is not part of the wire payload (V1 ignores it)
    assert "scope" not in patched_urlopen["body"]


async def test_end_to_end_post_filter_keeps_addressed_file(
    patched_urlopen: dict[str, Any],
) -> None:
    """Scout.rag_fetch over the HTTP backend keeps only the addressed file."""
    backend = RagAnythingHttpBackend()
    result = await rag_fetch(
        backend, Address(path="raw/reports/acme.pdf", hint="kerberoasting")
    )
    assert result.status is FetchStatus.OK
    assert {c.file_path for c in result.context} == {"raw/reports/acme.pdf"}


def test_to_chunk_handles_missing_fields() -> None:
    chunk = RagAnythingHttpBackend._to_chunk({"text": "x"})
    assert chunk.file_path == "" and chunk.score == 0.0 and chunk.meta == {}
    kept = post_filter([chunk], "")
    assert kept == [chunk]
