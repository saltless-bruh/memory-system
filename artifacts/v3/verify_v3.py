#!/usr/bin/env python3
"""Independent oracles for the V3 retrieval inversion gate ledger.

Each ``--group`` performs its own measurement against the repository or the
running stack and prints a success-only token **after** every assertion in that
group has passed. A group that is not yet implemented exits non-zero and prints
no token, so its gate cannot be credited before the work exists.

Nothing here reads a figure out of a report and echoes it back. Where a gate
asserts an absence, the group also exercises a known positive control, so a
checker that can no longer detect anything fails instead of certifying silence.

Usage:
    .venv/bin/python artifacts/v3/verify_v3.py --group <name>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_FILES = (".env", ".env.agent_local", ".env.example")
EMBED_KEY = "LITELLM_EMBED_MODEL"
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.integration.yml",
    "docker-compose.staging.yml",
)

# A pinned model name ends in an explicit numeric version segment. A floating
# alias (``-latest``, ``-stable``, or a bare family name) can be revised by the
# provider under a live index, which is the failure mode this rejects.
PINNED_VERSION_RE = re.compile(r"-\d{3,}$")
FLOATING_TOKENS = ("latest", "stable", "preview")


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def _read_env_models() -> dict[str, str]:
    """Return {env filename: configured embedding model} for files that set it."""
    found: dict[str, str] = {}
    for name in ENV_FILES:
        path = REPO_ROOT / name
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == EMBED_KEY:
                found[name] = value.strip().strip("'\"")
    return found


# --------------------------------------------------------------------------
# leaf-1.1.1 — model pinning
# --------------------------------------------------------------------------


def group_env_pin() -> str:
    models = _read_env_models()
    require(bool(models), f"no env file sets {EMBED_KEY}; nothing to verify")
    distinct = sorted(set(models.values()))
    require(
        len(distinct) == 1,
        f"{EMBED_KEY} disagrees across env files: {models!r}",
    )
    return "ENV PIN VERIFIED"


def group_env_version() -> str:
    models = _read_env_models()
    require(bool(models), f"no env file sets {EMBED_KEY}; nothing to verify")
    for name, model in sorted(models.items()):
        lowered = model.lower()
        for token in FLOATING_TOKENS:
            require(
                token not in lowered,
                f"{name} pins a floating alias ({model!r} contains {token!r})",
            )
        require(
            PINNED_VERSION_RE.search(model) is not None,
            f"{name} model {model!r} has no explicit numeric version suffix",
        )
    # Positive control: the detector must reject a known-floating name.
    control = "gemini/gemini-embedding-latest"
    control_rejected = any(t in control.lower() for t in FLOATING_TOKENS)
    require(control_rejected, "version detector failed its positive control")
    return "ENV VERSION VERIFIED"


# --------------------------------------------------------------------------
# leaf-1.1.2 — migration 006
# --------------------------------------------------------------------------


def group_migration_shape() -> str:
    migrations = REPO_ROOT / "config" / "postgres" / "migrations"
    # Same discovery glob the runner uses (scripts/migrate_postgres.py).
    discovered = sorted(migrations.glob("[0-9][0-9][0-9]_*.sql"))
    sixes = [p for p in discovered if p.name.startswith("006_")]
    require(len(sixes) == 1, f"expected exactly one 006_* migration, found {sixes!r}")
    body = sixes[0].read_text(encoding="utf-8")
    require(
        "metadata->>'corpus'" in body,
        "006 does not index metadata->>'corpus' (blocker B-2 unaddressed)",
    )
    require(
        "metadata->>'type'" in body,
        "006 does not index metadata->>'type' (per-class ranking has no index)",
    )
    require(
        "rag_chunks" in body,
        "006 does not reference rag_chunks",
    )
    return "MIGRATION SHAPE VERIFIED"


# --------------------------------------------------------------------------
# leaf-1.1.3 — compose rewire
# --------------------------------------------------------------------------


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose's merge tags (``!override``, ``!reset``).

    Compose overlay files carry these tags to control list and mapping merge
    behaviour. ``yaml.safe_load`` rejects them outright, which would make this
    oracle fail for a reason that has nothing to do with the gate.
    """


def _keep_underlying_value(
    loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node
) -> object:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return None


_ComposeLoader.add_multi_constructor("!", _keep_underlying_value)


def _load_compose(name: str) -> dict[str, object]:
    path = REPO_ROOT / name
    require(path.exists(), f"{name} is missing")
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # noqa: S506
    require(isinstance(loaded, dict), f"{name} did not parse to a mapping")
    assert isinstance(loaded, dict)
    return loaded


def group_compose_parse() -> str:
    for name in COMPOSE_FILES:
        _load_compose(name)
    return "COMPOSE PARSE VERIFIED"


def group_compose() -> str:
    saw_scout_mount = False
    for name in COMPOSE_FILES:
        doc = _load_compose(name)
        services = doc.get("services")
        if not isinstance(services, dict):
            continue
        require(
            "basic-memory" not in services,
            f"{name} still defines the basic-memory service (second vector space)",
        )
        scout = services.get("scout")
        if not isinstance(scout, dict):
            continue
        volumes = scout.get("volumes")
        if not isinstance(volumes, list):
            continue
        for entry in volumes:
            if isinstance(entry, str) and "vault-replica" in entry:
                require(
                    entry.rstrip().endswith(":ro"),
                    f"{name}: scout mounts vault-replica writable ({entry!r}); "
                    "the vault must be read-only to scout (INV-1)",
                )
                saw_scout_mount = True
    require(
        saw_scout_mount,
        "no compose file mounts vault-replica into scout; wiki_read cannot "
        "reach the vault on disk",
    )
    return "COMPOSE REWIRE VERIFIED"


# --------------------------------------------------------------------------
# leaf-1.3.3 — gen_index write guard
# --------------------------------------------------------------------------

_PAGE_WITH_SUMMARY = """---
type: concept
title: {title}
summary: {title} has a real one-line summary.
entities: [alpha]
department: redteam
sources: []
last_compiled: 2026-08-28
---

# {title}

## TL;DR
A sentence.

## Technical Specifications
Detail.

## Provenance
Source.

## Cross-References
- [[{other}]]
"""

_PAGE_WITHOUT_SUMMARY = """---
type: concept
title: {title}
entities: [alpha]
department: redteam
sources: []
last_compiled: 2026-08-28
---

# {title}

## TL;DR
A sentence.

## Technical Specifications
Detail.

## Provenance
Source.

## Cross-References
- [[{other}]]
"""


def _build_vault(root: Path, template: str) -> Path:
    wiki = root / "wiki"
    (wiki / "concepts").mkdir(parents=True, exist_ok=True)
    names = ["alpha", "beta"]
    for i, name in enumerate(names):
        other = names[(i + 1) % len(names)]
        (wiki / "concepts" / f"{name}.md").write_text(
            template.format(title=name, other=other), encoding="utf-8"
        )
    return wiki


def group_index_guard() -> str:
    sys.path.insert(0, str(REPO_ROOT))
    import scripts.gen_index as gen_index  # noqa: PLC0415

    guard = getattr(gen_index, "write_mode_allowed", None)
    require(
        callable(guard),
        "scripts/gen_index.py exposes no write_mode_allowed guard; write mode "
        "can still overwrite an authored catalogue with blank descriptions",
    )
    assert callable(guard)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # Positive control: a vault that legitimately carries summaries must
        # still be writable, otherwise the guard is merely refusing everything.
        good = _build_vault(root / "good", _PAGE_WITH_SUMMARY)
        allowed_good, _ = guard(good)
        require(
            allowed_good is True,
            "guard refuses a vault with full summary coverage; it is not "
            "discriminating, it is just blocking",
        )

        # The real assertion: a vault with no summaries must be refused.
        bad = _build_vault(root / "bad", _PAGE_WITHOUT_SUMMARY)
        allowed_bad, reason = guard(bad)
        require(
            allowed_bad is False,
            "guard permits write mode on a vault with zero summary coverage; "
            "302 authored descriptions would be overwritten with blanks",
        )
        require(bool(reason), "guard refused without stating a reason")

    return "INDEX GUARD VERIFIED"


# --------------------------------------------------------------------------
# Static quality, scoped to this branch
# --------------------------------------------------------------------------


# Untracked paths that predate this branch. `git` cannot distinguish "untracked
# because this branch created it" from "untracked and already sitting here", so
# the pre-existing ones are named explicitly rather than silently swept in.
# artifacts/audits/ is the 2026-08-27 feature-inventory scratch; it was present
# in the working tree before feat/v3-retrieval-inversion was cut.
_PRE_EXISTING_UNTRACKED = ("artifacts/audits/",)


def _branch_python_files() -> list[str]:
    """Python files this branch adds or modifies, tracked and untracked alike."""
    import subprocess  # noqa: PLC0415

    def run(*args: str) -> list[str]:
        proc = subprocess.run(
            args, cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )
        return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]

    changed = run(
        "git", "diff", "--name-only", "--diff-filter=d", "main...HEAD", "--", "*.py"
    )
    dirty = run("git", "diff", "--name-only", "--diff-filter=d", "--", "*.py")
    untracked = run("git", "ls-files", "--others", "--exclude-standard", "--", "*.py")
    untracked = [
        f
        for f in untracked
        if not any(f.startswith(prefix) for prefix in _PRE_EXISTING_UNTRACKED)
    ]
    seen = sorted({*changed, *dirty, *untracked})
    return [f for f in seen if (REPO_ROOT / f).exists()]


def group_static_branch() -> str:
    """Ruff and mypy over this branch's own Python, plus repo-wide mypy.

    Deliberately scoped. Repo-wide ``ruff check .`` currently fails on files
    that predate this branch, so a repo-wide oracle here would report a
    pre-existing condition as this branch's defect and invite explaining the
    failure away. The baseline is tracked separately as `static-repo`, which is
    allowed to stay unmet and visible.
    """
    import subprocess  # noqa: PLC0415

    files = _branch_python_files()
    require(bool(files), "no Python files changed on this branch; nothing to check")

    for tool_args in (("ruff", "check"), ("ruff", "format", "--check")):
        proc = subprocess.run(
            ["uv", "run", *tool_args, *files],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        require(
            proc.returncode == 0,
            f"{' '.join(tool_args)} failed on branch files:\n"
            f"{proc.stdout.strip() or proc.stderr.strip()}",
        )

    proc = subprocess.run(
        ["uv", "run", "mypy", "scout", "scripts"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    require(
        proc.returncode == 0,
        f"mypy strict failed:\n{proc.stdout.strip() or proc.stderr.strip()}",
    )
    return "STATIC BRANCH VERIFIED"


def group_static_repo() -> str:
    """Repo-wide ruff, including files this branch never touched.

    Expected to be unmet at the time of writing: `scripts/host_sync.py` (I001)
    and `tests/test_host_sync.py` (SIM117) were committed in 26abe20, and
    `artifacts/audits/feature-inventory-2026-08-27/direct_probes.py` carries six
    more. None is this branch's work, and none is fixed here without the owner
    asking, so this gate stays visible rather than being quietly narrowed.
    """
    import subprocess  # noqa: PLC0415

    for tool_args in (("ruff", "check", "."), ("ruff", "format", "--check", ".")):
        proc = subprocess.run(
            ["uv", "run", *tool_args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        require(
            proc.returncode == 0,
            f"{' '.join(tool_args)} failed repo-wide:\n"
            f"{(proc.stdout or proc.stderr).strip()[:600]}",
        )
    return "STATIC REPO VERIFIED"


# --------------------------------------------------------------------------
# leaf-1.2.1 — wiki body ingestion
# --------------------------------------------------------------------------


def group_body_index() -> str:
    """Prove wiki vectors are derived from body chunks, never ``summary``.

    The positive fixture omits ``summary`` entirely and carries unique markers
    only in its Markdown body. The negative control does the inverse: it puts a
    plausible routing sentence in frontmatter and supplies no body evidence.
    A detector that accidentally falls back to frontmatter therefore fails the
    control instead of certifying the original defect.
    """
    from scout.parsers import parse_markdown  # noqa: PLC0415
    from scout.wiki_ingest import (  # noqa: PLC0415
        IndexCatalog,
        WikiIngestError,
        ingest_wiki,
        prepare_wiki_document,
    )

    body_marker = "BODY_EVIDENCE_4f7c9d"
    with tempfile.TemporaryDirectory() as tmp:
        wiki = Path(tmp) / "vault"
        page_dir = wiki / "concepts"
        page_dir.mkdir(parents=True)
        (wiki / "index.md").write_text(
            """---
title: Catalogue
updated: 2026-08-28
---
""",
            encoding="utf-8",
        )
        (page_dir / "body-indexed-page.md").write_text(
            """---
title: Body Indexed Page
type: concept
updated: 2026-08-28
---

# Body Indexed Page

Lead prose is authored evidence.

## Details

The searchable body carries BODY_EVIDENCE_4f7c9d and no summary field exists.
""",
            encoding="utf-8",
        )

        class RecordingEmbedder:
            model = "gemini/gemini-embedding-001"
            dim = 1024

            def __init__(self) -> None:
                self.inputs: list[str] = []

            def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
                self.inputs.extend(texts)
                return [[0.0] * self.dim for _ in texts]

        class RecordingConnection:
            def __init__(self) -> None:
                self.chunk_texts: list[str] = []
                self.metadata: list[dict[str, object]] = []

            @asynccontextmanager
            async def transaction(self) -> AsyncIterator[None]:
                yield

            async def fetchrow(self, _query: str, *_args: object) -> dict[str, int]:
                return {"doc_id": 1}

            async def execute(self, query: str, *args: object) -> str:
                if query.strip().startswith("DELETE FROM rag_chunks"):
                    return "DELETE 0"
                if "INSERT INTO rag_chunks" in query:
                    self.chunk_texts.append(str(args[2]))
                    self.metadata.append(json.loads(str(args[5])))
                    return "INSERT 0 1"
                raise GateFailure(f"body-index control saw unexpected SQL: {query}")

            async def close(self) -> None:
                raise GateFailure("ingest_wiki closed a caller-owned connection")

        embedder = RecordingEmbedder()
        connection = RecordingConnection()
        results = asyncio.run(ingest_wiki(wiki, conn=connection, embedder=embedder))
        require(bool(results), "a body-bearing wiki page was not ingested")
        require(bool(connection.chunk_texts), "wiki ingestion inserted no chunks")
        require(
            all(text.strip() for text in connection.chunk_texts),
            "an inserted wiki chunk has empty body text",
        )
        require(
            all(text.strip() for text in embedder.inputs),
            "an embedding input is empty",
        )
        require(
            any(body_marker in text for text in embedder.inputs),
            "the unique body marker never reached an embedding input",
        )
        require(
            any(body_marker in text for text in connection.chunk_texts),
            "the unique body marker never reached the chunk insert",
        )
        require(
            all(row.get("corpus") == "wiki" for row in connection.metadata),
            "an inserted chunk is not stamped as the wiki corpus",
        )

    summary_only = parse_markdown(
        """---
title: Summary Only Control
type: concept
summary: This frontmatter sentence must never become embedded evidence.
updated: 2026-08-28
---
""",
        "concepts/summary-only-control.md",
    )
    try:
        prepare_wiki_document(summary_only, IndexCatalog.empty())
    except WikiIngestError:
        control_rejected = True
    else:
        control_rejected = False
    require(
        control_rejected,
        "positive control failed: a summary-only page was accepted as body text",
    )
    return "BODY INDEX VERIFIED"


# --------------------------------------------------------------------------
# leaf-1.2.2 — page retrieval, class RRF, and sparse degradation
# --------------------------------------------------------------------------


class _OracleAsyncEmbedder:
    async def aembed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * 1024 for _ in texts]


def _mock_pg_pool(
    fetch_side_effect: Callable[..., object],
) -> tuple[Any, Any]:
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.fetch = AsyncMock(side_effect=fetch_side_effect)

    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=None)
    connection.transaction.return_value = transaction

    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=connection)
    acquire.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    return pool, connection


def _retrieval_row(
    path: str,
    *,
    page_type: str = "concept",
    score: float = 0.1,
) -> dict[str, object]:
    return {
        "chunk_id": f"chunk:{path}",
        "chunk_text": f"Body for {path}",
        "source_uri": path,
        "metadata": {"loc": "Section Detail", "type": page_type},
        "rrf_score": score,
    }


def group_page_dedup() -> str:
    from scout.backends.pgvector import PgVectorRlsBackend  # noqa: PLC0415
    from scout.types import Scope  # noqa: PLC0415

    async def fetch_rows(_query: str, *_args: object) -> list[dict[str, object]]:
        return [_retrieval_row("wiki/alpha.md"), _retrieval_row("wiki/beta.md")]

    pool, connection = _mock_pg_pool(fetch_rows)
    backend = PgVectorRlsBackend(embedder=_OracleAsyncEmbedder(), pool=pool)
    chunks = asyncio.run(
        backend.retrieve(
            "controlled page query",
            scope=Scope(departments=frozenset({"infra"})),
            k=5,
        )
    )
    require(len(chunks) == 2, "controlled page retrieval did not return both pages")

    call = connection.fetch.call_args
    query = str(call.args[0])
    normalized = " ".join(query.split())
    detector = re.compile(r"DISTINCT\s+ON\s*\(\s*doc_id\s*\)", re.IGNORECASE)
    require(
        detector.search(normalized) is not None,
        "retrieval SQL does not apply DISTINCT ON (doc_id) before the final limit",
    )
    require(
        normalized.index("DISTINCT ON") > normalized.index("combined AS"),
        "document grouping is not downstream of combined hybrid candidates",
    )
    candidate_k = int(call.args[3])
    require(candidate_k >= 20, f"hybrid retrieval only widened to {candidate_k}")

    old_shape = "WITH combined AS (SELECT 1) SELECT * FROM combined LIMIT 5"
    require(
        detector.search(old_shape) is None,
        "page-dedup detector failed its known pre-change control",
    )
    return "PAGE LEVEL DEDUP VERIFIED"


def group_class_rank() -> str:
    from scout.backends.pgvector import PgVectorRlsBackend  # noqa: PLC0415
    from scout.types import Scope  # noqa: PLC0415

    queries: list[str] = []

    async def ranked_rows(query: str, *args: object) -> list[dict[str, object]]:
        queries.append(query)
        base = int(cast(int, args[5]))
        raw_delta = int(cast(int, args[6]))
        curated_score = 1.0 / (base + 2)
        raw_score = 1.0 / (base + raw_delta + 1)
        candidates = [
            _retrieval_row("wiki/curated.md", page_type="concept", score=curated_score),
            _retrieval_row("raw/source.md", page_type="raw", score=raw_score),
        ]
        return sorted(
            candidates,
            key=lambda row: float(cast(float, row["rrf_score"])),
            reverse=True,
        )

    async def run(delta: int) -> list[str]:
        pool, _connection = _mock_pg_pool(ranked_rows)
        backend = PgVectorRlsBackend(
            embedder=_OracleAsyncEmbedder(),
            pool=pool,
            raw_rank_penalty=delta,
        )
        chunks = await backend.retrieve(
            "same controlled query",
            scope=Scope(departments=frozenset({"infra"})),
            k=5,
        )
        return [chunk.file_path for chunk in chunks]

    preferred = asyncio.run(run(15))
    zero_control = asyncio.run(run(0))
    require(
        preferred != zero_control,
        "class ordering is identical with and without the raw penalty",
    )
    require(
        preferred[0] == "wiki/curated.md" and zero_control[0] == "raw/source.md",
        f"unexpected class-control order: penalty={preferred}, zero={zero_control}",
    )

    normalized = " ".join(queries[0].split())
    type_detector = re.compile(r"->>\s*'type'", re.IGNORECASE)
    require(
        type_detector.search(normalized) is not None,
        "RRF does not read document type",
    )
    require(
        re.search(r"=\s*'raw'", normalized, re.IGNORECASE) is not None,
        "RRF has no explicit raw-class branch",
    )
    require("$7" in normalized, "configured raw penalty is not used by SQL")
    require(
        "unknown" not in normalized.casefold(),
        "untyped pages are explicitly demoted instead of using the baseline",
    )
    require(
        type_detector.search("1.0 / (60 + rank)") is None,
        "class-rank detector failed its known pre-change control",
    )
    return "CLASS RANK VERIFIED"


def group_degradation() -> str:
    from scout.backends.pgvector import PgVectorRlsBackend  # noqa: PLC0415
    from scout.types import Scope  # noqa: PLC0415

    scope = Scope(departments=frozenset({"infra"}))

    async def healthy_rows(_query: str, *_args: object) -> list[dict[str, object]]:
        return [_retrieval_row("wiki/healthy.md")]

    healthy_pool, _healthy_connection = _mock_pg_pool(healthy_rows)
    healthy_backend = PgVectorRlsBackend(
        embedder=_OracleAsyncEmbedder(),
        pool=healthy_pool,
    )
    healthy = asyncio.run(healthy_backend.retrieve("health control", scope=scope))
    require(bool(healthy), "healthy dense control returned no result")
    require(
        healthy[0].meta.get("degraded") == "false",
        "healthy dense control was not explicitly marked non-degraded",
    )

    class TimeoutEmbedder:
        async def aembed_texts(self, _texts: Sequence[str]) -> list[list[float]]:
            raise TimeoutError("controlled timeout")

    sparse_queries: list[str] = []

    async def sparse_rows(query: str, *_args: object) -> list[dict[str, object]]:
        sparse_queries.append(query)
        return [_retrieval_row("wiki/sparse.md")]

    sparse_pool, _sparse_connection = _mock_pg_pool(sparse_rows)
    sparse_backend = PgVectorRlsBackend(
        embedder=TimeoutEmbedder(),
        pool=sparse_pool,
        dense_timeout_seconds=0.01,
    )
    sparse = asyncio.run(sparse_backend.retrieve("fallback control", scope=scope))
    require(bool(sparse), "embedding timeout returned no sparse result")
    require(
        sparse[0].meta.get("degraded") == "true",
        "sparse fallback result is not marked degraded",
    )
    require(
        sparse[0].meta.get("degraded_reason") == "embedding_timeout",
        "sparse fallback does not carry the timeout reason code",
    )
    require(bool(sparse_queries), "embedding timeout never executed sparse SQL")
    require(
        "vector_matches" not in sparse_queries[0],
        "degraded query still depends on the dense vector arm",
    )
    return "DEGRADATION VERIFIED"


# --------------------------------------------------------------------------
# leaf-1.2.3 — scoped engine and removal of the local index
# --------------------------------------------------------------------------


def group_scope() -> str:
    from scout.diy_engine import ScoutDiyEngine  # noqa: PLC0415
    from scout.types import RagChunk, Scope  # noqa: PLC0415

    class CompatibleEmbedder(_OracleAsyncEmbedder):
        async def embed(self, texts: Sequence[str]) -> list[list[float]]:
            return await self.aembed_texts(texts)

    class RecordingBackend:
        def __init__(self) -> None:
            self.scopes: list[Scope | None] = []

        async def retrieve(
            self,
            _hint: str,
            *,
            path: str | None = None,
            scope: Scope | None = None,
            k: int = 10,
        ) -> Sequence[RagChunk]:
            del path, k
            self.scopes.append(scope)
            return [
                RagChunk(
                    text="Scoped body result",
                    file_path="concepts/scoped-page.md",
                    score=0.5,
                    meta={
                        "content_hash": "hash:scoped",
                        "tldr": "Scoped result",
                        "type": "concept",
                    },
                )
            ]

    with tempfile.TemporaryDirectory() as tmp:
        wiki = Path(tmp) / "vault"
        page_dir = wiki / "concepts"
        page_dir.mkdir(parents=True)
        (page_dir / "scoped-page.md").write_text(
            """---
title: Scoped Page
type: concept
updated: 2026-08-28
---
# Scoped Page

## TL;DR
Scoped result.
""",
            encoding="utf-8",
        )
        backend = RecordingBackend()
        engine = ScoutDiyEngine.from_vault(
            CompatibleEmbedder(),
            wiki_dir=wiki,
            rag_backend=backend,
        )

        async def exercise() -> tuple[bool, list[object]]:
            try:
                await engine.wiki_search("scope control")
            except ValueError:
                refused = True
            else:
                refused = False
            scope = Scope(departments=frozenset({"infra"}))
            hits = await engine.wiki_search("scope control", scope=scope)
            return refused, list(hits)

        refused, hits = asyncio.run(exercise())
        require(refused, "wiki_search accepted a scope-less query")
        require(bool(hits), "scoped wiki_search returned no page")
        require(
            len(backend.scopes) == 1
            and backend.scopes[0] is not None
            and backend.scopes[0].departments == frozenset({"infra"}),
            f"scope did not reach the backend exactly once: {backend.scopes!r}",
        )
        require(
            getattr(hits[0], "path", None) == "concepts/scoped-page.md",
            "backend result was not adapted to a wiki page hit",
        )
    return "SCOPE VERIFIED"


def group_workflow_scope() -> str:
    """Prove orchestration cannot erase authority at an adapter boundary."""
    from scout.diy_engine import WikiHit, WikiPage  # noqa: PLC0415
    from scout.types import RagChunk, Scope  # noqa: PLC0415
    from scout.workflow import WikiEngine, answer_query  # noqa: PLC0415

    scope = Scope(departments=frozenset({"infra"}))

    class EmptyBackend:
        async def retrieve(
            self,
            _hint: str,
            *,
            path: str | None = None,
            scope: Scope | None = None,
            k: int = 10,
        ) -> Sequence[RagChunk]:
            del path, scope, k
            return ()

    class ScopedWiki:
        def __init__(self) -> None:
            self.search_scopes: list[Scope] = []
            self.read_scopes: list[Scope] = []

        async def wiki_search(
            self,
            _query: str,
            _k: int = 5,
            *,
            seen: Sequence[str] = (),
            scope: Scope,
        ) -> Sequence[WikiHit]:
            del seen
            self.search_scopes.append(scope)
            return [
                WikiHit(
                    page_id="scoped",
                    path="concepts/scoped.md",
                    score=1.0,
                    summary="Scoped page",
                )
            ]

        async def wiki_read(
            self,
            _path: str,
            *,
            mode: str = "full",
            section: str | None = None,
            scope: Scope,
        ) -> WikiPage:
            del mode, section
            self.read_scopes.append(scope)
            return WikiPage(body="Scoped body")

    class ScopeDroppingWiki:
        """Known pre-fix shape: its methods cannot receive authority."""

        async def wiki_search(self, _query: str, _k: int = 5) -> Sequence[WikiHit]:
            return [
                WikiHit(
                    page_id="unscoped",
                    path="concepts/unscoped.md",
                    score=1.0,
                    summary="Unscoped page",
                )
            ]

        async def wiki_read(self, _path: str) -> WikiPage:
            return WikiPage(body="Unscoped body")

    async def exercise() -> tuple[ScopedWiki, bool, bool]:
        scoped = ScopedWiki()
        await answer_query(
            scoped,
            EmptyBackend(),
            "scope forwarding control",
            scope=scope,
        )
        try:
            await answer_query(
                cast(WikiEngine, ScopeDroppingWiki()),
                EmptyBackend(),
                "scope dropping control",
                scope=scope,
            )
        except TypeError as exc:
            rejected = "scope" in str(exc)
        else:
            rejected = False
        no_scope = ScopedWiki()
        try:
            await answer_query(
                no_scope,
                EmptyBackend(),
                "missing scope control",
                scope=cast(Scope, None),
            )
        except ValueError as exc:
            none_rejected = "authenticated scope" in str(exc)
        else:
            none_rejected = False
        require(
            not no_scope.search_scopes and not no_scope.read_scopes,
            "the workflow touched the wiki before rejecting an empty scope",
        )
        return scoped, rejected, none_rejected

    scoped, rejected, none_rejected = asyncio.run(exercise())
    require(
        len(scoped.search_scopes) == 1
        and scoped.search_scopes[0] is scope
        and len(scoped.read_scopes) == 1
        and scoped.read_scopes[0] is scope,
        "the workflow did not forward the exact scope to both wiki calls",
    )
    require(
        rejected,
        "the workflow accepted an adapter that silently drops authenticated scope",
    )
    require(none_rejected, "the workflow accepted an empty authenticated scope")
    return "WORKFLOW SCOPE VERIFIED"


def group_engine_local_index_absence() -> str:
    source = (REPO_ROOT / "scout" / "diy_engine.py").read_text(encoding="utf-8")
    forbidden = (
        "import sqlite3",
        "sqlite3.",
        "CREATE VIRTUAL TABLE",
        "fts5(",
        "search.db",
        "vector_cache.json",
        "_fts_conn",
    )
    survivors = [token for token in forbidden if token in source]
    require(not survivors, f"local index implementation survives: {survivors!r}")

    old_control = (
        "import sqlite3\n"
        "sqlite3.connect('search.db').execute("
        "'CREATE VIRTUAL TABLE x USING fts5(body)')\n"
        "cache = 'vector_cache.json'\n"
    )
    detected = [token for token in forbidden if token in old_control]
    require(
        len(detected) >= 5,
        "local-index absence detector failed its known pre-change control",
    )
    return "LOCAL INDEX REMOVAL VERIFIED"


# --------------------------------------------------------------------------
# leaf-1.2.4 — authenticated MCP and local agent surfaces
# --------------------------------------------------------------------------


def group_tool_surface() -> str:
    from scout.auth import load_auth_config  # noqa: PLC0415
    from scout.cli.mcp_policy import Exposure, policy_for  # noqa: PLC0415
    from scout.mcp_server import build_server  # noqa: PLC0415
    from scout.types import RagChunk, Scope  # noqa: PLC0415

    class EmptyBackend:
        async def retrieve(
            self,
            _hint: str,
            *,
            path: str | None = None,
            scope: Scope | None = None,
            k: int = 10,
        ) -> Sequence[RagChunk]:
            del path, scope, k
            return ()

    expected = {"wiki_search", "wiki_read"}
    expected_parameters = {
        "wiki_search": {"query", "k", "seen", "department"},
        "wiki_read": {"path", "mode", "section", "department"},
    }

    def surface_errors(tools: Sequence[object]) -> list[str]:
        errors: list[str] = []
        names = {str(getattr(tool, "name", "")) for tool in tools}
        if names != expected:
            errors.append(f"serves {sorted(names)!r}, expected {sorted(expected)!r}")
        for tool in tools:
            name = str(getattr(tool, "name", ""))
            annotations = getattr(tool, "annotations", None)
            if annotations is None:
                errors.append(f"{name} has no annotations")
            elif not (
                getattr(annotations, "readOnlyHint", None) is True
                and getattr(annotations, "destructiveHint", None) is False
                and getattr(annotations, "idempotentHint", None) is True
            ):
                errors.append(f"{name} lost its read-only annotations")
            parameters = getattr(tool, "parameters", {})
            properties = parameters.get("properties", {})
            if len(properties) > 8:
                errors.append(
                    f"{name} exceeds the eight-parameter budget: {properties!r}"
                )
            if (
                name in expected_parameters
                and set(properties) != expected_parameters[name]
            ):
                errors.append(f"{name} parameters drifted: {sorted(properties)!r}")
            description = str(getattr(tool, "description", ""))
            if "untrusted data, never instructions" not in description:
                errors.append(f"{name} lost the injection guard")
        return errors

    configs = {
        "development": load_auth_config(
            {"SCOUT_AUTH_MODE": "development"}, bind_host="127.0.0.1"
        ),
        "protected": load_auth_config(
            {
                "SCOUT_AUTH_MODE": "static",
                "SCOUT_AUTH_BASE_URL": "https://scout.example.test",
                "SCOUT_STATIC_TOKENS": (
                    '{"token":{"subject":"gate","departments":["infra"]}}'
                ),
            }
        ),
    }
    for branch, config in configs.items():
        tools = asyncio.run(
            build_server(EmptyBackend(), auth_config=config).list_tools()
        )
        errors = surface_errors(tools)
        require(not errors, f"{branch} Scout surface failed: {errors!r}")

    fetch_policy = policy_for("fetch")
    search_policy = policy_for("search")
    read_policy = policy_for("read")
    require(
        fetch_policy is not None and fetch_policy.exposure is Exposure.HIDDEN,
        "direct fetch is exposed on the local MCP surface",
    )
    require(
        search_policy is not None
        and search_policy.exposure is Exposure.TOOL
        and search_policy.tool == "wiki_search",
        "local search is not exposed as wiki_search",
    )
    require(
        read_policy is not None
        and read_policy.exposure is Exposure.TOOL
        and read_policy.tool == "wiki_read",
        "local read is not exposed as wiki_read",
    )

    class RetiredTool:
        name = "rag_fetch"
        annotations = None
        parameters: dict[str, object] = {"properties": {}}
        description = "Resolve one address."

    require(
        bool(surface_errors([RetiredTool()])),
        "surface detector missed the known retired rag_fetch control",
    )
    return "TOOL SURFACE VERIFIED"


# --------------------------------------------------------------------------
# leaf-1.2.5 — shipped agent contracts and mirrors
# --------------------------------------------------------------------------


def group_contracts() -> str:
    agent_root = REPO_ROOT / ".agent"
    claude_root = REPO_ROOT / ".claude"
    package_root = REPO_ROOT / "packages" / "snp-agent"
    contract_roots = (agent_root, claude_root, package_root)
    contract_dirs = {"instructions", "rules", "skills", "workflows"}
    package_files = sorted(path for path in package_root.rglob("*") if path.is_file())
    require(bool(package_files), "portable agent package is empty")

    active_files = [REPO_ROOT / "AGENTS.md", REPO_ROOT / "CLAUDE.md"]
    active_files.extend(
        path for path in package_files if path.suffix in {".md", ".json"}
    )
    for root in (agent_root, claude_root):
        active_files.extend(
            path
            for subdir in contract_dirs
            for path in (root / subdir).rglob("*")
            if path.is_file()
            and path.suffix in {".md", ".json"}
            and not any(part.startswith("superpowers") for part in path.parts)
        )

    retired_patterns = (
        (
            "sufficiency-stop",
            re.compile(
                r"\bif\b.{0,160}\bwiki(?:\s+page)?\b.{0,160}\banswers?\b"
                r".{0,80}\bstop\b.{0,120}\b(?:do\s+not|don't|never)\b"
                r".{0,60}\b(?:query|call|use)\b.{0,60}\b(?:rag|scout)\b",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
        ("R-5.1", re.compile(r"\bR-5\.1\b|sufficiency\s+stop", re.IGNORECASE)),
        ("R-6.3", re.compile(r"\bR-6\.3\b", re.IGNORECASE)),
        (
            "address-minting",
            re.compile(
                r"sources\[\]\.hint|scripts/mint\.py|verify_addresses\.py|"
                r"ci_address_gate\.py",
                re.IGNORECASE,
            ),
        ),
        (
            "old-golden-rule",
            re.compile(
                r"wiki\s+tells\s+you\s+where\s+to\s+go.{0,80}"
                r"rag\s+gives\s+you\s+the\s+verbatim\s+source",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
        (
            "retired-tool",
            re.compile(
                r"\b(?:rag_fetch|search_notes|read_note|write_note|list_notes)\b",
                re.IGNORECASE,
            ),
        ),
        (
            "retired-healer",
            re.compile(r"snp-auto-heal-vault|snp-heal\.md", re.IGNORECASE),
        ),
    )

    def retired_violations(paths: Sequence[Path]) -> list[str]:
        violations: list[str] = []
        for path in paths:
            content = path.read_text(encoding="utf-8")
            try:
                display = path.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                display = path.name
            for label, pattern in retired_patterns:
                if pattern.search(content):
                    violations.append(f"{display}:{label}")
        return violations

    with tempfile.TemporaryDirectory(prefix="v3-contract-control-") as temporary:
        control = Path(temporary) / "retired-rule.md"
        control.write_text(
            "If the wiki page answers the question, STOP. Do not query RAG.\n",
            encoding="utf-8",
        )
        detected = retired_violations([control])
        require(
            any(item.endswith(":sufficiency-stop") for item in detected),
            "contract detector missed the planted stop-before-RAG behavior",
        )

    violations = retired_violations(active_files)
    require(
        not violations,
        "retired agent contracts remain: "
        + repr(violations[:20])
        + (f" (+{len(violations) - 20} more)" if len(violations) > 20 else ""),
    )

    ordered_contracts = (
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "CLAUDE.md",
        package_root / "instructions" / "query_protocol.instructions.md",
        package_root / "workflows" / "snp-query.md",
        package_root / "skills" / "snp-rag-fetch" / "SKILL.md",
    )
    for path in ordered_contracts:
        content = path.read_text(encoding="utf-8")
        search_at = content.find("wiki_search")
        read_at = content.find("wiki_read")
        require(
            0 <= search_at < read_at,
            f"{path.relative_to(REPO_ROOT)} does not establish wiki_search -> wiki_read",
        )

    safety_contracts = (
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "CLAUDE.md",
        *(root / "rules" / "snp-memory.md" for root in contract_roots),
    )
    injection_guard = re.compile(
        r"(?:untrusted\s+)?data.{0,30}(?:not|never)\s+instructions",
        re.IGNORECASE | re.DOTALL,
    )
    scope_guard = re.compile(
        r"(?:narrow.{0,80}(?:cannot|never).{0,30}(?:add|expand)|"
        r"(?:cannot|never).{0,30}(?:add|expand).{0,80}narrow)",
        re.IGNORECASE | re.DOTALL,
    )
    for path in safety_contracts:
        content = path.read_text(encoding="utf-8")
        require(
            bool(injection_guard.search(content)),
            f"{path.relative_to(REPO_ROOT)} lost the injection boundary",
        )
        require(
            bool(scope_guard.search(content)),
            f"{path.relative_to(REPO_ROOT)} lost the request-scope boundary",
        )

    expected_tools = {
        "scout": ["wiki_search", "wiki_read"],
        "snpmemory": [
            "verify",
            "plan_articles",
            "compile_plan",
            "compile_status",
            "wiki_search",
            "wiki_read",
        ],
    }
    expected_skills = {
        "snp-bootstrap-system",
        "snp-compile-wiki",
        "snp-export-mcp",
        "snp-ingest-raw-data",
        "snp-rag-fetch",
        "snp-search-wiki",
        "snp-verify-vault",
    }
    expected_workflows = {
        "snp-compile.md",
        "snp-ingest.md",
        "snp-query.md",
        "snp-reload.md",
        "snp-verify.md",
    }
    for root in (package_root, agent_root):
        plugin_path = root / "plugin.json"
        mcp_path = root / "mcp.json"
        require(
            plugin_path.is_file(),
            f"missing mirror {plugin_path.relative_to(REPO_ROOT)}",
        )
        require(
            mcp_path.is_file(),
            f"missing mirror {mcp_path.relative_to(REPO_ROOT)}",
        )
        plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        extension = plugin["extensions"]["io.snp.memory"]
        declared_tools = {
            name: value
            for name, value in extension["requiredTools"].items()
            if name != "note"
        }
        require(
            declared_tools == expected_tools,
            f"{plugin_path.relative_to(REPO_ROOT)} tool manifest drifted",
        )
        ships = extension["ships"]
        require(
            set(ships["skills"]) == expected_skills,
            f"{plugin_path.relative_to(REPO_ROOT)} skill inventory drifted",
        )
        require(
            set(ships["workflows"]) == expected_workflows,
            f"{plugin_path.relative_to(REPO_ROOT)} workflow inventory drifted",
        )
        mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
        require(
            set(mcp["mcpServers"]) == set(expected_tools),
            f"{mcp_path.relative_to(REPO_ROOT)} still declares a retired server",
        )

    for package_path in package_files:
        relative = package_path.relative_to(package_root)
        agent_path = agent_root / relative
        require(
            agent_path.is_file(),
            f"missing mirror {agent_path.relative_to(REPO_ROOT)}",
        )
        require(
            agent_path.read_bytes() == package_path.read_bytes(),
            f"mirror drift: {agent_path.relative_to(REPO_ROOT)}",
        )
        if relative.parts[0] in contract_dirs:
            claude_path = claude_root / relative
            require(
                claude_path.is_file(),
                f"missing mirror {claude_path.relative_to(REPO_ROOT)}",
            )
            require(
                claude_path.read_bytes() == package_path.read_bytes(),
                f"mirror drift: {claude_path.relative_to(REPO_ROOT)}",
            )

    def mirrored_contract_files(root: Path) -> set[Path]:
        return {
            path.relative_to(root)
            for subdir in contract_dirs
            for path in (root / subdir).rglob("*")
            if path.is_file()
            and not any(part.startswith("superpowers") for part in path.parts)
        }

    agent_contracts = mirrored_contract_files(agent_root)
    claude_contracts = mirrored_contract_files(claude_root)
    require(
        agent_contracts == claude_contracts,
        "active .agent/.claude contract inventories differ: "
        f"agent-only={sorted(map(str, agent_contracts - claude_contracts))!r}, "
        f"claude-only={sorted(map(str, claude_contracts - agent_contracts))!r}",
    )
    for relative in agent_contracts:
        require(
            (agent_root / relative).read_bytes()
            == (claude_root / relative).read_bytes(),
            f"mirror drift: {relative}",
        )

    for root in contract_roots:
        require(
            not (root / "skills" / "snp-auto-heal-vault").exists(),
            f"retired healer skill remains under {root.relative_to(REPO_ROOT)}",
        )
        require(
            not (root / "workflows" / "snp-heal.md").exists(),
            f"retired healer workflow remains under {root.relative_to(REPO_ROOT)}",
        )

    return "CONTRACTS VERIFIED"


# --------------------------------------------------------------------------
# Groups for later waves. These fail until their leaf lands.
# --------------------------------------------------------------------------

_PENDING: dict[str, str] = {
    "rebuild": "leaf-1.3.1",
    "model-stamp": "leaf-1.1.1 plus a live index",
    "index-preservation": "leaf-1.3.3 plus the reference corpus",
}


def _pending(group: str) -> Callable[[], str]:
    def run() -> str:
        raise GateFailure(
            f"group {group!r} is not implemented yet; it lands with "
            f"{_PENDING[group]}. This gate must not be credited."
        )

    return run


GROUPS: dict[str, Callable[[], str]] = {
    "env-pin": group_env_pin,
    "env-version": group_env_version,
    "migration-shape": group_migration_shape,
    "compose": group_compose,
    "compose-parse": group_compose_parse,
    "index-guard": group_index_guard,
    "body-index": group_body_index,
    "page-dedup": group_page_dedup,
    "class-rank": group_class_rank,
    "degradation": group_degradation,
    "scope": group_scope,
    "workflow-scope": group_workflow_scope,
    "engine-local-index-absence": group_engine_local_index_absence,
    "tool-surface": group_tool_surface,
    "contracts": group_contracts,
    "static-branch": group_static_branch,
    "static-repo": group_static_repo,
}
for _name in _PENDING:
    GROUPS[_name] = _pending(_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, choices=sorted(GROUPS))
    args = parser.parse_args()

    try:
        token = GROUPS[args.group]()
    except GateFailure as exc:
        print(f"FAIL [{args.group}] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - surface any oracle defect loudly
        print(f"ERROR [{args.group}] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
