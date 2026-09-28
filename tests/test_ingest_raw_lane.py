"""The raw lane must scale with what changed, not with the corpus.

Three faults together made every change under `raw/` expensive and fragile:

* no signature short-circuit -- only the vault tier skipped unchanged pages, so
  every raw document was re-parsed and re-embedded on every change;
* the synchronous, urllib-based `embed_texts` ran inside `async def
  ingest_document`, on the event loop the vault watcher and the readiness
  aggregator share, so the whole process stalled for every embed;
* `_embed_one_batch` had no retry while the vision call retried five times, so
  one transient 429/503 raised, rolled the batch back, and spent one of
  `sync_once`'s three attempts re-embedding everything again.
"""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from email.message import Message
from pathlib import Path
from typing import Any

import httpx
import pytest

from scout.capabilities import capability_fingerprint
from scout.chunker import EmbeddingError, LiteLLMBatchEmbedder
from scout.ingest import (
    ContextualChunker,
    ingest_directory,
    ingest_document,
    raw_chunk_policy,
)

_MODEL = "snp-embed"


class _CountingEmbedder:
    """A synchronous embedder that records the work it was asked to do."""

    model = _MODEL
    dim = 1024

    def __init__(self) -> None:
        self.embedded: list[str] = []
        self.threads: list[threading.Thread] = []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.threads.append(threading.current_thread())
        self.embedded.extend(texts)
        return [[float(i == 0) for i in range(self.dim)] for _ in texts]


class _Connection:
    """Answers the raw-tier signature query and records every write."""

    def __init__(self, signatures: list[dict[str, object]] | None = None) -> None:
        self.signatures = signatures or []
        self.chunk_metadata: list[dict[str, Any]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetch(self, query: str, *_args: object) -> list[dict[str, object]]:
        if "content_hash" in query:
            return list(self.signatures)
        return []

    async def fetchrow(self, _query: str, *_args: object) -> dict[str, int]:
        return {"doc_id": 1}

    async def execute(self, query: str, *args: object) -> str:
        if "INSERT INTO rag_chunks" in query:
            self.chunk_metadata.append(json.loads(str(args[5])))
        return "DELETE 0" if query.lstrip().startswith("DELETE") else "OK"

    async def close(self) -> None:
        return None


def _corpus(tmp_path: Path, body: str = "The raw source body.") -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.md").write_text(f"# A\n\n{body}\n", encoding="utf-8")
    return raw


def _signature(raw: Path, **overrides: object) -> dict[str, object]:
    """The signature a previous, complete ingest of `raw/a.md` would have left."""
    row: dict[str, object] = {
        "source_uri": "raw/a.md",
        "allowed_depts": ["infra"],
        "capability_fingerprint": json.dumps(capability_fingerprint()),
        "extraction_status": json.dumps({"extractors": {}, "complete": True}),
        "chunks": 1,
        "hashes": 1,
        "content_hash": hashlib.sha256((raw / "a.md").read_bytes()).hexdigest(),
        "models": 1,
        "model": _MODEL,
        "policies": 1,
        "chunk_policy": raw_chunk_policy(ContextualChunker()),
    }
    row.update(overrides)
    return row


async def _ingest(
    raw: Path, monkeypatch: pytest.MonkeyPatch, conn: _Connection
) -> tuple[list[dict[str, Any]], _CountingEmbedder]:
    async def connect(_env: object = None) -> _Connection:
        return conn

    monkeypatch.setattr("scout.ingest.get_pg_connection", connect)
    embedder = _CountingEmbedder()
    results = await ingest_directory(
        raw, ["infra"], reconcile=False, embedder=embedder, env={}
    )
    return results, embedder


# ── the signature short-circuit ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unchanged_raw_document_is_not_re_embedded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _corpus(tmp_path)
    conn = _Connection([_signature(raw)])

    results, embedder = await _ingest(raw, monkeypatch, conn)

    assert [(r["source_uri"], r["status"]) for r in results] == [
        ("raw/a.md", "unchanged")
    ]
    assert embedder.embedded == []
    assert conn.chunk_metadata == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"content_hash": "0" * 64},
        {"allowed_depts": ["redteam"]},
        {"model": "another-route"},
        {"chunk_policy": "chars=1;overlap=0;rev=0"},
        {"hashes": 2},
        {"capability_fingerprint": json.dumps({"parser": "another"})},
        {
            "extraction_status": json.dumps(
                {"extractors": {"figures": "failed"}, "complete": False}
            )
        },
    ],
    ids=[
        "edited",
        "acl-changed",
        "other-model",
        "other-policy",
        "disagreeing-chunks",
        "other-parser",
        "degraded-extraction",
    ],
)
async def test_anything_that_changes_what_would_be_written_re_embeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    """The controls: a short-circuit that swallows a real change serves stale
    evidence -- or, for an ACL change, keeps a revoked grant readable."""
    raw = _corpus(tmp_path)
    conn = _Connection([_signature(raw, **overrides)])

    results, embedder = await _ingest(raw, monkeypatch, conn)

    assert [r["status"] for r in results] == ["ingested_ok"]
    assert embedder.embedded != []


@pytest.mark.asyncio
async def test_raw_chunks_record_what_the_short_circuit_compares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the stamps on the chunks, the next cycle could never match."""
    raw = _corpus(tmp_path)
    conn = _Connection()

    await _ingest(raw, monkeypatch, conn)

    assert conn.chunk_metadata
    for metadata in conn.chunk_metadata:
        assert metadata["content_hash"] == _signature(raw)["content_hash"]
        assert metadata["chunk_policy"] == raw_chunk_policy(ContextualChunker())
        assert "corpus" not in metadata, "the raw tier is the unstamped one"


# ── the embed never blocks the event loop ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_synchronous_embedder_runs_off_the_event_loop(
    tmp_path: Path,
) -> None:
    source = tmp_path / "a.md"
    source.write_text("# A\n\nbody\n", encoding="utf-8")
    embedder = _CountingEmbedder()

    await ingest_document(
        source, ["infra"], conn=_Connection(), embedder=embedder, base_dir=tmp_path
    )

    assert embedder.threads
    assert all(t is not threading.main_thread() for t in embedder.threads)


@pytest.mark.asyncio
async def test_an_async_embedder_is_awaited_not_called_synchronously(
    tmp_path: Path,
) -> None:
    source = tmp_path / "a.md"
    source.write_text("# A\n\nbody\n", encoding="utf-8")

    class _AsyncOnly(_CountingEmbedder):
        def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
            raise AssertionError("the blocking path must not be taken")

        async def aembed_texts(self, texts: Sequence[str]) -> list[list[float]]:
            self.embedded.extend(texts)
            return [[float(i == 0) for i in range(self.dim)] for _ in texts]

    embedder = _AsyncOnly()
    result = await ingest_document(
        source, ["infra"], conn=_Connection(), embedder=embedder, base_dir=tmp_path
    )

    assert result["status"] == "ingested_ok"
    assert embedder.embedded


# ── one transient gateway failure is retried ───────────────────────────────


def _vectors(count: int) -> dict[str, object]:
    return {"data": [{"index": i, "embedding": [0.5] * 1024} for i in range(count)]}


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scout.gateway_retry.backoff_delay", lambda *_a, **_k: 0.0)


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_backoff")
async def test_the_async_embed_survives_one_transient_gateway_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter([httpx.Response(503), httpx.Response(429), None])
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        reply = next(replies)
        return reply if reply is not None else httpx.Response(200, json=_vectors(2))

    _route_async_client(monkeypatch, handler)
    embedder = LiteLLMBatchEmbedder(base_url="http://gateway/v1", api_key="k")

    vectors = await embedder.aembed_texts(["one", "two"])

    assert len(vectors) == 2
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_backoff")
async def test_the_async_embed_does_not_retry_a_request_the_gateway_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400)

    _route_async_client(monkeypatch, handler)
    embedder = LiteLLMBatchEmbedder(base_url="http://gateway/v1", api_key="k")

    with pytest.raises(EmbeddingError):
        await embedder.aembed_texts(["one"])
    assert len(calls) == 1


@pytest.mark.usefixtures("no_backoff")
def test_the_sync_embed_survives_one_transient_gateway_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    class _Body:
        def __enter__(self) -> _Body:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(_vectors(1)).encode()

    def urlopen(request: urllib.request.Request, **_kwargs: object) -> _Body:
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 503, "unavailable", hdrs=Message(), fp=None
            )
        return _Body()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    embedder = LiteLLMBatchEmbedder(base_url="http://gateway/v1", api_key="k")

    assert len(embedder.embed_texts(["one"])) == 1
    assert len(calls) == 2


def test_the_cli_does_not_count_an_unchanged_document_as_indexed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`unchanged` rows keep their chunk count; the run spent nothing on them."""
    from scout.cli.commands.ingest import ingest
    from scout.cli.config import Config
    from scout.cli.registry import Prerequisite
    from scout.cli.result import ExitCode

    repo = tmp_path.resolve()
    (repo / "raw").mkdir()
    (repo / "raw" / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    (repo / "raw" / ".acl.yaml").write_text(
        'version: 1\nrules:\n  - path: "**"\n    departments: [ai_eng]\n',
        encoding="utf-8",
    )

    async def pipeline(**_kwargs: object) -> list[dict[str, object]]:
        return [
            {"source_uri": "raw/a.md", "chunks_count": 4, "status": "unchanged"},
            {"source_uri": "raw/b.md", "chunks_count": 2, "status": "ingested_ok"},
        ]

    monkeypatch.setattr("scout.ingest.ingest_directory", pipeline)
    config = Config(prerequisite=Prerequisite.LOCAL, values={}, repo_root=repo)

    result = ingest(dir="raw", confirm=True, config=config)

    assert result.exit_code == ExitCode.SUCCESS
    assert result.data["indexed"] == 1
    assert result.data["unchanged"] == 1
    assert "1 unchanged" in result.summary


def _route_async_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr("scout.chunker.httpx.AsyncClient", client)
