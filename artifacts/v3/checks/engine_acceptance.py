#!/usr/bin/env python3
"""Behavioural acceptance oracles for the engine's two claims.

Every other check in this scope measures shape: that a function exists, that a
contract file says the right thing, that a unit behaves against a fake. These
measure the two things the owner actually asks of the system.

  * **W-1** the agent queries the index and gets back an address and a file.
  * **W-2** a change in the vault reaches the index.

They run against the live stack through the shipped surfaces -- the production
`PgVectorRlsBackend`, the real corpus, the real gateway -- because the failures
worth catching here are the ones a double cannot reproduce. The prior
integration oracle modelled PostgreSQL in Python and could not have seen any of
them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scout.chunker import LiteLLMBatchEmbedder  # noqa: E402
from scout.diy_engine import ScoutDiyEngine  # noqa: E402
from scout.policy import CANONICAL_DEPARTMENTS  # noqa: E402
from scout.types import Scope  # noqa: E402

QUESTIONS = REPO_ROOT / "artifacts" / "v3" / "retrieval_questions.json"

#: The served surface. W-2's claim is about *the next answer*, and answers come
#: from the scout container, which reads the replica -- not from this machine's
#: copy of the vault. Speaking only MCP here is also what makes the measurement
#: honest about H-1: the probe page never exists on the disk this process can
#: see, so a passing result cannot have come from a local read.
SCOUT_URL = os.environ.get("SNP_SCOUT_URL", "http://127.0.0.1:8080/mcp")
SCOUT_TOKENS = REPO_ROOT / ".secrets" / "scout_static_tokens.json"
VAULT_REMOTE = os.environ.get(
    "SNP_VAULT_REMOTE", "http://127.0.0.1:3000/snp-admin/snp-memory.git"
)
VAULT_BRANCH = os.environ.get("GIT_BRANCH", "main")
#: Where the vault lives inside the repository that holds it.
VAULT_SUBDIR = "wiki"
#: How long a change may take to travel push -> webhook -> replica -> index.
PROPAGATION_TIMEOUT_SECONDS = 180.0
PROPAGATION_POLL_SECONDS = 5.0
#: How long the deployment gate waits for a restarted container's health to
#: settle. Measured cold start is about 90 seconds; this is that with room.
HEALTH_SETTLE_SECONDS = 180.0
VAULT = Path(
    os.environ.get("SNP_REFERENCE_VAULT")
    or Path.home() / "Documents" / "memo-project" / "Obsidian Vault"
)

#: Read from constants, never from the measured result. A floor derived from
#: what was observed is not a floor.
RECALL_AT_1_FLOOR = 0.60
RECALL_AT_5_FLOOR = 0.85
SEARCH_K = 5


class _Fetches(Protocol):
    """The one thing a census needs from a database connection."""

    async def fetch(self, query: str, *args: Any) -> Any: ...


class GateFailure(AssertionError):
    """A measured engine outcome did not hold."""


def require(condition: bool, message: str) -> None:
    """Raise a gate-specific failure when `condition` is false."""
    if not condition:
        raise GateFailure(message)


@dataclass(frozen=True)
class Question:
    lang: str
    query: str
    expect: str
    control: str | None = None


def load_questions() -> tuple[list[Question], list[Question]]:
    """Return the measurable questions and the controls, separately."""
    raw = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    require(isinstance(raw, list) and bool(raw), f"{QUESTIONS}: empty question set")
    parsed = [
        Question(
            lang=str(e["lang"]),
            query=str(e["query"]),
            expect=str(e["expect"]),
            control=str(e["control"]) if "control" in e else None,
        )
        for e in raw
    ]
    measurable = [q for q in parsed if q.control is None]
    controls = [q for q in parsed if q.control == "absent"]
    require(
        len(measurable) >= 40,
        f"question set has {len(measurable)} measurable entries, expected at least 40",
    )
    require(
        bool(controls),
        "no absent-page control in the question set; this gate could not tell "
        "present from absent",
    )
    languages = {q.lang for q in measurable}
    require(
        "vi" in languages,
        "no Vietnamese questions: a dense-timeout regression once made every "
        "Vietnamese query return nothing while every mechanical gate stayed green",
    )
    return measurable, controls


def _build_engine() -> ScoutDiyEngine:
    """The shipped engine, on the production backend. No doubles."""
    embedder = LiteLLMBatchEmbedder(
        base_url=os.environ.get("LITELLM_BASE_URL"),
        api_key=os.environ.get("LITELLM_MASTER_KEY"),
    )
    return ScoutDiyEngine.from_vault(embedder, wiki_dir=VAULT)


def _headings(page: object) -> list[str]:
    """The headings a citation could name, from the page's own outline."""
    outline = getattr(page, "outline", ()) or ()
    found: list[str] = []
    for entry in outline:
        if isinstance(entry, dict):
            heading = entry.get("heading") or entry.get("title") or entry.get("loc")
            if isinstance(heading, str) and heading.strip():
                found.append(heading.strip())
    return found


async def _find_read_cite() -> str:
    engine = _build_engine()
    scope = Scope(departments=frozenset(CANONICAL_DEPARTMENTS))
    measurable, controls = load_questions()

    ranks: list[int | None] = []
    returned_paths: set[str] = set()
    try:
        for question in measurable:
            hits = await engine.wiki_search(question.query, k=SEARCH_K, scope=scope)
            paths = [hit.path for hit in hits]
            returned_paths.update(paths)
            ranks.append(
                paths.index(question.expect) + 1 if question.expect in paths else None
            )

        # The control decides whether any of the above means anything. An
        # oracle that scores a page nobody has as "found" is measuring its own
        # optimism.
        for control in controls:
            hits = await engine.wiki_search(control.query, k=SEARCH_K, scope=scope)
            found = [hit.path for hit in hits]
            require(
                control.expect not in found,
                f"the absent-page control was scored as found ({control.expect}); "
                "this gate cannot tell present from absent",
            )

        require(
            bool(returned_paths),
            "no search returned any page at all; the gate cannot observe its subject",
        )

        # Every page search offered must actually be readable. This is the
        # check that would have caught the 431-versus-7 vault fork, where
        # search returned five correct identities and read raised KeyError on
        # every one of them: find and read disagreed about what the vault held.
        unreadable: list[str] = []
        uncitable: list[str] = []
        for path in sorted(returned_paths):
            try:
                page = await engine.wiki_read(path, mode="full", scope=scope)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                unreadable.append(f"{path}: {type(exc).__name__}: {exc}")
                continue
            if not (getattr(page, "body", "") or "").strip():
                unreadable.append(f"{path}: read returned an empty body")
                continue
            headings = _headings(page)
            if not headings:
                uncitable.append(path)
                continue
            # An answer cites a heading; that heading has to exist in the page
            # it claims to come from, or the citation is unverifiable.
            body = page.body
            missing = [h for h in headings if h not in body]
            if missing:
                uncitable.append(
                    f"{path}: outline names {missing[:2]} absent from body"
                )
    finally:
        await engine.aclose()

    require(
        not unreadable,
        f"{len(unreadable)} of {len(returned_paths)} returned pages could not be "
        f"read: {unreadable[:3]}",
    )
    require(
        not uncitable,
        f"{len(uncitable)} returned pages cannot be cited by heading: {uncitable[:3]}",
    )

    def found_at(n: int) -> float:
        return sum(1 for r in ranks if r is not None and r <= n) / len(ranks)

    at1, at5 = found_at(1), found_at(SEARCH_K)
    require(
        at1 >= RECALL_AT_1_FLOOR,
        f"recall@1 {at1:.2f} < {RECALL_AT_1_FLOOR:.2f}",
    )
    require(
        at5 >= RECALL_AT_5_FLOOR,
        f"recall@{SEARCH_K} {at5:.2f} < {RECALL_AT_5_FLOOR:.2f}",
    )
    for lang in sorted({q.lang for q in measurable}):
        subset = [r for q, r in zip(measurable, ranks, strict=True) if q.lang == lang]
        lang_at5 = sum(1 for r in subset if r is not None and r <= SEARCH_K) / len(
            subset
        )
        require(
            lang_at5 >= RECALL_AT_5_FLOOR,
            f"{lang} recall@{SEARCH_K} {lang_at5:.2f} < {RECALL_AT_5_FLOOR:.2f}",
        )

    print(
        f"  {len(measurable)} questions, {len(returned_paths)} distinct pages "
        f"returned and read; recall@1={at1:.2f} recall@{SEARCH_K}={at5:.2f}; "
        f"{len(controls)} absent-page control(s) correctly missed"
    )
    return "FIND READ CITE VERIFIED"


def group_find_read_cite() -> str:
    """Search finds the page, read returns its body, and the citation resolves."""
    return asyncio.run(_find_read_cite())


# --------------------------------------------------------------------------
# W-2 -- a change in the vault reaches the index
# --------------------------------------------------------------------------


def _scout_token() -> str:
    """The widest-scoped static token, read at run time and never printed."""
    require(SCOUT_TOKENS.is_file(), f"no scout tokens at {SCOUT_TOKENS}")
    tokens = json.loads(SCOUT_TOKENS.read_text(encoding="utf-8"))
    require(bool(tokens), f"{SCOUT_TOKENS}: no tokens configured")
    widest = max(tokens.items(), key=lambda kv: len(kv[1].get("departments", ())))
    return str(widest[0])


class _Scout:
    """The agent-facing MCP surface, spoken to exactly as an agent would."""

    def __init__(self) -> None:
        self._token = _scout_token()

    async def _call(self, tool: str, arguments: dict[str, object]) -> object:
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        transport = StreamableHttpTransport(
            SCOUT_URL, headers={"Authorization": f"Bearer {self._token}"}
        )
        async with Client(transport) as client:
            result = await client.call_tool(tool, arguments)
        payload = result.structured_content or result.data
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"]
        return payload

    async def search(self, query: str, k: int = 5) -> list[dict[str, object]]:
        hits = await self._call("wiki_search", {"query": query, "k": k})
        return list(hits) if isinstance(hits, list) else []

    async def read(self, path: str) -> dict[str, object]:
        page = await self._call("wiki_read", {"path": path, "mode": "full"})
        return page if isinstance(page, dict) else {}


def _git(*args: str, cwd: Path) -> str:
    """Run one git command, failing the gate rather than the process."""
    completed = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    require(
        completed.returncode == 0,
        f"git {' '.join(args)} failed: {completed.stderr.strip()[:300]}",
    )
    return completed.stdout


def _probe_page(sentinel: str) -> tuple[str, str]:
    """A self-contained page carrying the sentinel, and its vault-relative path."""
    body = (
        "---\n"
        f"title: {sentinel}\n"
        "type: concept\n"
        "---\n\n"
        "## TL;DR\n\n"
        f"Propagation probe {sentinel}. This page is written by the W-2\n"
        "acceptance oracle and removed by it in the same run.\n\n"
        "## Cross-References\n\n"
        "[[index]]\n"
    )
    return f"{sentinel}.md", body


def _publish(clone: Path, relative: str, body: str | None, message: str) -> None:
    """Write or delete one vault page and push it to the vault branch."""
    target = clone / VAULT_SUBDIR / relative
    if body is None:
        target.unlink(missing_ok=True)
    else:
        target.write_text(body, encoding="utf-8")
    _git("add", "-A", f"{VAULT_SUBDIR}/{relative}", cwd=clone)
    _git(
        "-c",
        "user.name=snp-engine-acceptance",
        "-c",
        "user.email=xanx404@gmail.com",
        "commit",
        "-m",
        message,
        cwd=clone,
    )
    _git("push", "origin", f"HEAD:{VAULT_BRANCH}", cwd=clone)


async def _await_sentinel(scout: _Scout, sentinel: str, budget: float) -> str | None:
    """Poll the served surface for the sentinel, running no other command.

    Running anything else here -- an ingest, a compose restart, a manual sync --
    would make the result say nothing about whether the loop closes on its own.
    """
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        for hit in await scout.search(sentinel, k=5):
            path = str(hit.get("path", ""))
            if sentinel in path or sentinel in str(hit.get("snippet", "")):
                return path
        await asyncio.sleep(PROPAGATION_POLL_SECONDS)
    return None


def _compose(*args: str) -> None:
    completed = subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    require(
        completed.returncode == 0,
        f"docker compose {' '.join(args)} failed: {completed.stderr.strip()[:300]}",
    )


async def _vault_change_propagates() -> str:
    scout = _Scout()
    clone = Path(tempfile.mkdtemp(prefix="v3-vault-probe-"))
    watcher_stopped = False
    try:
        _git(
            "clone",
            "--depth",
            "1",
            "--branch",
            VAULT_BRANCH,
            VAULT_REMOTE,
            ".",
            cwd=clone,
        )

        # ---- positive: an edit reaches the index with no command run ----
        sentinel = f"V3-PROP-{uuid.uuid4().hex}"
        relative, body = _probe_page(sentinel)
        require(
            await _await_sentinel(scout, sentinel, budget=0.0) is None,
            f"{sentinel} was already findable before it was written; this gate "
            "would prove nothing",
        )
        _publish(clone, relative, body, f"test(w2): propagation probe {sentinel}")
        started = time.monotonic()
        found = await _await_sentinel(scout, sentinel, PROPAGATION_TIMEOUT_SECONDS)
        elapsed = time.monotonic() - started
        require(
            found is not None,
            f"{sentinel} never became findable within "
            f"{PROPAGATION_TIMEOUT_SECONDS:.0f}s of the push",
        )
        page = await scout.read(str(found))
        rendered = json.dumps(page, ensure_ascii=False)
        require(
            sentinel in rendered,
            f"search returned {found} but reading it did not contain {sentinel}: "
            "find and read disagree about what the vault holds",
        )
        _publish(
            clone, relative, None, f"test(w2): remove propagation probe {sentinel}"
        )

        # ---- negative: with the watcher stopped, the same push must NOT arrive ----
        # Without this the gate cannot tell "the loop works" from "the loop
        # happened to already be in the right state". Same timeout as the
        # positive leg, or a miss would only mean it was not given long enough.
        _compose("stop", "sync-job")
        watcher_stopped = True
        blocked = f"V3-PROP-{uuid.uuid4().hex}"
        blocked_rel, blocked_body = _probe_page(blocked)
        _publish(
            clone,
            blocked_rel,
            blocked_body,
            f"test(w2): negative control probe {blocked}",
        )
        leaked = await _await_sentinel(scout, blocked, PROPAGATION_TIMEOUT_SECONDS)
        _publish(
            clone,
            blocked_rel,
            None,
            f"test(w2): remove negative control probe {blocked}",
        )
        require(
            leaked is None,
            f"{blocked} became findable at {leaked} while the vault watcher was "
            "stopped; something other than sync-job is indexing, so the positive "
            "leg proves nothing about this loop",
        )
    finally:
        if watcher_stopped:
            _compose("start", "sync-job")
        shutil.rmtree(clone, ignore_errors=True)

    print(
        f"  probe reached the served surface {elapsed:.0f}s after the push, and "
        f"did not arrive at all in {PROPAGATION_TIMEOUT_SECONDS:.0f}s with the "
        "watcher stopped"
    )
    return "VAULT CHANGE PROPAGATES"


def group_vault_change_propagates() -> str:
    """An edit pushed to the vault repository reaches the served index."""
    return asyncio.run(_vault_change_propagates())


# --------------------------------------------------------------------------
# The shipped SQL, against real PostgreSQL
# --------------------------------------------------------------------------


def _app_role_connection_settings() -> object:
    from scout.config import postgres_settings

    return postgres_settings("query", env=os.environ)


async def _live_sql() -> str:
    import asyncpg

    from scout.backends.pgvector import PgVectorRlsBackend

    embedder = LiteLLMBatchEmbedder(
        base_url=os.environ.get("LITELLM_BASE_URL"),
        api_key=os.environ.get("LITELLM_MASTER_KEY"),
    )
    cleared = Scope(departments=frozenset(CANONICAL_DEPARTMENTS))
    # Two hints, because they answer different questions. Ranking and dedup
    # need a query the *vault* answers richly. The tier check needs one only
    # the raw corpus answers: the single raw-tier document is a deep-learning
    # paper and the vault has nothing on that subject, so a leak shows up as
    # the paper appearing where it must not.
    hint = "headless browser automation"
    raw_hint = "convolutional neural networks for recognising objects in images"

    wiki = PgVectorRlsBackend(embedder=embedder, corpus="wiki")
    unfiltered = PgVectorRlsBackend(embedder=embedder, corpus=None)
    try:
        rows = list(await wiki.retrieve(hint, scope=cleared, k=10))
        require(bool(rows), "the wiki-tier query returned nothing at all")

        # 1. page_best is really deduplicating by document, in SQL.
        paths = [row.file_path for row in rows]
        require(
            len(paths) == len(set(paths)),
            f"the same page came back more than once: "
            f"{sorted({p for p in paths if paths.count(p) > 1})}",
        )

        # 2. Real fusion, not the constant the Python double returned.
        scores = {round(row.score, 12) for row in rows}
        require(
            len(scores) > 1,
            f"every rrf_score was identical ({scores}); this is the constant the "
            "hermetic double produced, not a fused ranking",
        )
        require(
            all(row.score > 0 for row in rows),
            "a returned row scored zero, so ranking cannot have ordered it",
        )

        # 3. The corpus tier holds, and the check can tell -- because the same
        #    query through an unfiltered backend, in this same process, does
        #    reach the raw document. Without that half, "the wiki tier returned
        #    no raw pages" would also be true of a backend returning nothing.
        loose = [
            row.file_path
            for row in await unfiltered.retrieve(raw_hint, scope=cleared, k=10)
        ]
        require(
            any(p.startswith("raw/papers/") for p in loose),
            "an unfiltered backend could not reach the raw corpus either, so the "
            f"tier check proves nothing; it returned {loose[:3]}",
        )
        tiered = [
            row.file_path for row in await wiki.retrieve(raw_hint, scope=cleared, k=10)
        ]
        leaked = [p for p in (*paths, *tiered) if p.startswith("raw/papers/")]
        require(
            not leaked,
            f"the wiki tier reached raw-corpus documents: {sorted(set(leaked))}",
        )

        # 4. The shipped surface is fail-closed without a scope.
        unscoped = list(await wiki.retrieve(hint, scope=None, k=10))
        require(
            not unscoped,
            f"a scope-less query returned {len(unscoped)} rows; RLS is not "
            "fail-closed at the shipped surface",
        )
    finally:
        await wiki.close()
        await unfiltered.close()

    # 5. And the gate can *observe* fail-closed rather than be blinded by it.
    #    Zero rows means nothing on its own -- that reading produced two
    #    confident "the database is empty" diagnoses against a full index. So
    #    the same connection must be shown returning rows once cleared.
    settings = _app_role_connection_settings()
    conn = await asyncpg.connect(
        host=settings.host,  # type: ignore[attr-defined]
        port=settings.port,  # type: ignore[attr-defined]
        database=settings.database,  # type: ignore[attr-defined]
        user=settings.user,  # type: ignore[attr-defined]
        password=settings.password,  # type: ignore[attr-defined]
    )
    try:
        blind = await conn.fetchval("SELECT count(*) FROM rag_chunks;")
        await conn.execute(
            "SELECT set_config('scout.current_depts', $1, false);",
            ",".join(sorted(CANONICAL_DEPARTMENTS)),
        )
        visible = await conn.fetchval("SELECT count(*) FROM rag_chunks;")
    finally:
        await conn.close()

    require(
        blind == 0,
        f"an uncleared app-role connection read {blind} chunks; RLS is not fail-closed",
    )
    require(
        visible > 0,
        "the same connection read nothing after clearance either, so this gate "
        "cannot tell fail-closed RLS from an empty database",
    )

    print(
        f"  {len(rows)} distinct pages, {len(scores)} distinct rrf scores; the "
        f"raw paper is reachable unfiltered and not through the wiki tier; "
        f"uncleared read {blind} chunks and cleared read {visible}"
    )
    return "LIVE SQL VERIFIED"


def group_live_sql() -> str:
    """The shipped retrieval SQL runs on real PostgreSQL under real RLS."""
    return asyncio.run(_live_sql())


# --------------------------------------------------------------------------
# Deployment and index coherence
# --------------------------------------------------------------------------


def _docker(*args: str) -> str:
    completed = subprocess.run(
        ["docker", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    require(
        completed.returncode == 0,
        f"docker {' '.join(args)} failed: {completed.stderr.strip()[:200]}",
    )
    return completed.stdout


def group_deployment() -> str:
    """The vault indexer is deployed, and the second vector space is gone."""
    listed = _docker("ps", "-a", "--format", "{{.Names}}\t{{.State}}").splitlines()
    names = {
        line.split("\t")[0]: line.split("\t")[-1] for line in listed if line.strip()
    }

    require(
        "snp-memory-sync-job-1" in names,
        "sync-job has no container; it was declared `restart: unless-stopped` "
        "and had never run, which is why nothing indexed the vault",
    )
    require(
        names["snp-memory-sync-job-1"] == "running",
        f"sync-job is {names['snp-memory-sync-job-1']}, not running",
    )
    # `starting` is not a verdict. This container's cold start re-reads both
    # corpora, which was measured at about 90 seconds, so a single sample taken
    # just after a restart reports `starting` and then `unhealthy` before the
    # first probe ever succeeds. Waiting for the status to settle keeps the
    # assertion intact -- a container that is genuinely broken still stays
    # unhealthy and still fails here -- while removing a flake that would
    # otherwise teach a reader to re-run the gate until it goes green.
    deadline = time.monotonic() + HEALTH_SETTLE_SECONDS
    health = "starting"
    while time.monotonic() < deadline:
        health = _docker(
            "inspect", "snp-memory-sync-job-1", "--format", "{{.State.Health.Status}}"
        ).strip()
        if health == "healthy":
            break
        time.sleep(2.0)
    require(
        health == "healthy",
        f"sync-job still reports {health} after {HEALTH_SETTLE_SECONDS:.0f}s",
    )

    orphans = sorted(n for n in names if "basic-memory" in n)
    require(
        not orphans,
        f"the FastEmbed@384 orphan is still running: {orphans}. Two vector "
        "spaces in one deployment is F-2",
    )

    # Declared is not deployed: read the process and the markers from inside.
    processes = _docker(
        "exec",
        "snp-memory-sync-job-1",
        "sh",
        "-c",
        'for p in /proc/[0-9]*; do [ -r $p/cmdline ] && tr "\\0" " " < $p/cmdline '
        "&& echo; done",
    )
    require(
        "scout.sync_job" in processes,
        "no scout.sync_job process inside the container",
    )
    markers = _docker(
        "exec", "snp-memory-sync-job-1", "sh", "-c", "ls /tmp/snp-sync-job/"
    ).split()
    for marker in ("ready", "ready.raw", "ready.wiki"):
        require(
            marker in markers,
            f"readiness marker {marker!r} is absent; markers present: {markers}",
        )

    print(f"  sync-job {health}, markers {sorted(markers)}, no basic-memory container")
    return "DEPLOYMENT VERIFIED"


async def _model_stamp() -> str:
    import asyncpg

    from scout.serve import _expected_embedding_model, assert_single_embedding_model

    expected = _expected_embedding_model()
    settings = _app_role_connection_settings()
    conn = await asyncpg.connect(
        host=settings.host,  # type: ignore[attr-defined]
        port=settings.port,  # type: ignore[attr-defined]
        database=settings.database,  # type: ignore[attr-defined]
        user=settings.user,  # type: ignore[attr-defined]
        password=settings.password,  # type: ignore[attr-defined]
    )
    try:
        await conn.execute(
            "SELECT set_config('scout.current_depts', $1, false);",
            ",".join(sorted(CANONICAL_DEPARTMENTS)),
        )
        census = {
            row["model"]: int(row["n"])
            for row in await conn.fetch(
                "SELECT c.metadata->>'model' AS model, count(*) AS n "
                "FROM rag_chunks c GROUP BY 1;"
            )
        }
        await assert_single_embedding_model(conn, expected_model=expected)
    finally:
        await conn.close()

    require(bool(census), "the census saw no chunks at all")
    require(
        None not in census,
        f"{census.get(None)} chunks carry no model stamp, so the guard against "
        "two vector spaces does not cover them",
    )
    require(
        set(census) == {expected},
        f"the index holds {sorted(census)} but this process embeds with {expected!r}",
    )

    # The guard has to be able to fail on this very data, or its acceptance
    # above says nothing. Plant a second space in a census of the real one.
    class _Mixed:
        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            return [
                {"model": expected, "n": sum(census.values())},
                {"model": "fastembed/bge-small-en-v1.5", "n": 1},
            ]

    detected = False
    try:
        await assert_single_embedding_model(_Mixed(), expected_model=expected)
    except RuntimeError:
        detected = True
    require(
        detected,
        "the startup guard accepted a census holding two embedding models; it "
        "cannot detect the failure it exists to prevent",
    )

    print(
        f"  {sum(census.values())} chunks, all stamped {expected!r}; "
        "guard rejects a planted second model"
    )
    return "MODEL STAMP VERIFIED"


def group_model_stamp() -> str:
    """One embedding model in the index, and a guard that can see otherwise."""
    return asyncio.run(_model_stamp())


# --------------------------------------------------------------------------
# Ingest integrity -- a corpus built while an extractor was down
# --------------------------------------------------------------------------

#: What the parser records about each optional extractor. `ok` means it ran and
#: covered everything it found; `no_evidence` means it looked and the document
#: has none. The rest are degradations that produce a *smaller document* and
#: are logged rather than raised, so nothing downstream notices.
HEALTHY_EXTRACTOR_STATES = frozenset({"ok", "no_evidence"})
_EXTRACTOR_KEYS = ("figures_status", "tables_status")


async def _assert_ingest_integrity(conn: _Fetches) -> str:
    """Read the extractor census from `conn` and refuse a degraded corpus.

    Takes the connection rather than opening one so a control can plant a
    degraded state inside a transaction and have this observe it. Opening its
    own would put the plant on another connection, where an uncommitted change
    is invisible -- a control that cannot reach the code it is testing reports
    "gate passed" and looks exactly like a broken gate.
    """
    census: dict[str, dict[str, int]] = {}
    for key in _EXTRACTOR_KEYS:
        rows = await conn.fetch(
            f"SELECT c.metadata->>'{key}' AS state, count(*) AS n "  # noqa: S608
            "FROM rag_chunks c GROUP BY 1;"
        )
        census[key] = {
            str(r["state"]): int(r["n"]) for r in rows if r["state"] is not None
        }
    # `described < found` is a degradation the coarse status can still call
    # `ok` in older rows, so read the pair as well as the flag.
    coverage = await conn.fetch(
        "SELECT DISTINCT c.metadata->>'figure_count' AS found, "
        "c.metadata->>'figures_described' AS described "
        "FROM rag_chunks c WHERE c.metadata ? 'figure_count';"
    )

    require(
        any(census[key] for key in _EXTRACTOR_KEYS),
        "no chunk records an extractor status at all, so this gate cannot "
        "observe its subject; the corpus may predate the status metadata",
    )
    broken = {
        key: {
            state: n
            for state, n in states.items()
            if state not in HEALTHY_EXTRACTOR_STATES
        }
        for key, states in census.items()
    }
    broken = {key: states for key, states in broken.items() if states}
    require(
        not broken,
        f"chunks were ingested while an extractor was degraded: {broken}. The "
        "parser logs this and returns a smaller document, so the corpus is "
        "quietly incomplete and nothing downstream can tell",
    )
    short = [
        (r["found"], r["described"])
        for r in coverage
        if r["described"] is not None and r["found"] != r["described"]
    ]
    require(not short, f"a document describes fewer figures than it found: {short}")

    print(
        f"  extractor states {census}; figure coverage "
        f"{[(r['found'], r['described']) for r in coverage]}"
    )
    return "INGEST INTEGRITY VERIFIED"


async def _ingest_integrity() -> str:
    import asyncpg

    settings = _app_role_connection_settings()
    conn = await asyncpg.connect(
        host=settings.host,  # type: ignore[attr-defined]
        port=settings.port,  # type: ignore[attr-defined]
        database=settings.database,  # type: ignore[attr-defined]
        user=settings.user,  # type: ignore[attr-defined]
        password=settings.password,  # type: ignore[attr-defined]
    )
    try:
        await conn.execute(
            "SELECT set_config('scout.current_depts', $1, false);",
            ",".join(sorted(CANONICAL_DEPARTMENTS)),
        )
        return await _assert_ingest_integrity(conn)
    finally:
        await conn.close()


def group_ingest_integrity() -> str:
    """No chunk was ingested while a document extractor was degraded."""
    return asyncio.run(_ingest_integrity())


# --------------------------------------------------------------------------
# CI -- workflows that are retained must actually gate something
# --------------------------------------------------------------------------

#: The workflows this repository keeps in order to gate changes. `pr-heal`'s
#: workflow was deleted in 29f1f50 and is deliberately not listed.
GATING_WORKFLOWS = ("checks.yaml", "security.yaml")

#: Gitea records a job that was never dispatched with `started` at the Unix
#: epoch while still marking its run complete. Reading the run status alone
#: therefore reports 15 successful `checks` runs for a repository whose CI has
#: never executed a single job -- which is exactly the reading that produced
#: the claim this gate exists to replace.
_JOB_STARTED_SQL = """
    SELECT r.workflow_id AS workflow, j.name AS job, j.status AS status,
           j.started AS started
    FROM action_run r JOIN action_run_job j ON j.run_id = r.id
    WHERE j.started > 0
    ORDER BY j.started DESC;
"""


def group_ci_executed() -> str:
    """The retained CI workflows have each actually run a job."""
    query = _JOB_STARTED_SQL.replace("\n", " ").strip()
    out = _docker(
        "exec",
        "snp-memory-git-1",
        "sh",
        "-c",
        # A copy, because Gitea holds a write lock on the live database.
        f'cp /data/gitea/gitea.db /tmp/ci.db && sqlite3 /tmp/ci.db "{query}"'
        " ; rm -f /tmp/ci.db",
    )
    # 3 = success, 4 = failure. Both mean the job ran to a verdict, which is
    # what "gated something" means. 5 (cancelled) and 6 (skipped) mean it did
    # not, however healthy the runner looked while not running it.
    reached_verdict = {"3", "4"}
    verdicts: dict[str, list[str]] = {}
    attempted: dict[str, list[str]] = {}
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 4 or not parts[0]:
            continue
        workflow, job, status = parts[0], parts[1], parts[2]
        attempted.setdefault(workflow, []).append(f"{job}:status={status}")
        if status in reached_verdict:
            verdicts.setdefault(workflow, []).append(f"{job}:status={status}")

    require(
        bool(attempted),
        "no CI job has ever started on this instance. Gitea marks runs complete "
        "even when no runner ever took them, so run status is not evidence; this "
        "reads the job's own start time",
    )
    missing = [w for w in GATING_WORKFLOWS if w not in verdicts]
    require(
        not missing,
        f"these retained workflows have never run a job to a verdict: {missing}. "
        f"Jobs that started at all: {dict(attempted)} "
        "(status 5 is cancelled and 6 is skipped -- neither gates anything)",
    )

    counts = {k: len(v) for k, v in verdicts.items()}
    print(f"  jobs reaching a verdict, by workflow: {counts}")
    return "CI EXECUTED VERIFIED"


GROUPS: dict[str, Callable[[], str]] = {
    "ci-executed": group_ci_executed,
    "ingest-integrity": group_ingest_integrity,
    "find-read-cite": group_find_read_cite,
    "vault-change-propagates": group_vault_change_propagates,
    "live-sql": group_live_sql,
    "deployment": group_deployment,
    "model-stamp": group_model_stamp,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=sorted(GROUPS), required=True)
    args = parser.parse_args(argv)
    try:
        print(GROUPS[args.group]())
    except GateFailure as exc:
        print(f"FAIL [{args.group}] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
