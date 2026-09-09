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
import datetime as dt
import json
import math
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
#: A latency sample must not be quantized by the five-second correctness-gate
#: poll. Each request still performs the real query embedding and served search.
LATENCY_POLL_SECONDS = 0.25
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
        payload = await self._call("wiki_search", {"query": query, "k": k})
        hits = payload.get("results") if isinstance(payload, dict) else payload
        return list(hits) if isinstance(hits, list) else []

    async def read(self, path: str) -> dict[str, object]:
        page = await self._call("wiki_read", {"path": path, "mode": "full"})
        return page if isinstance(page, dict) else {}


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one git command, failing the gate rather than the process."""
    completed = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check:
        require(
            completed.returncode == 0,
            f"git {' '.join(args)} failed: {completed.stderr.strip()[:300]}",
        )
    return completed


#: How many times to re-attempt a push that lost a race. The gate shares its
#: branch with the vault lane, so a rejection is ordinary traffic rather than
#: an error -- but a branch that never stops moving is a real problem and
#: should surface rather than spin.
_PUSH_ATTEMPTS = 4


@dataclass(frozen=True)
class _PublishedChange:
    """The Git identity and wall-clock bounds of one successful push."""

    commit: str
    push_started_at: float
    push_completed_at: float


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


def _publish(
    clone: Path, relative: str, body: str | None, message: str
) -> _PublishedChange:
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
    push_started_at = time.time()
    commit = _push_with_rebase(clone)
    return _PublishedChange(
        commit=commit,
        push_started_at=push_started_at,
        push_completed_at=time.time(),
    )


def _push_with_rebase(clone: Path) -> str:
    """Push to the vault branch, re-basing onto whatever else landed.

    This gate pushes to the same branch the vault lane merges into, so `main`
    moving between the clone and the push is ordinary traffic, not a fault. It
    happened on 2026-09-07 and failed a gate whose subject was working
    perfectly.

    Never force. The competing push is somebody's authored work -- on that
    occasion 50 pages of it -- and a gate that discards content to record its
    own success has done far more damage than the failure it avoided.
    """
    for attempt in range(1, _PUSH_ATTEMPTS + 1):
        pushed = _git("push", "origin", f"HEAD:{VAULT_BRANCH}", cwd=clone, check=False)
        if pushed.returncode == 0:
            return _git("rev-parse", "HEAD", cwd=clone).stdout.strip()
        stderr = pushed.stderr.strip()
        require(
            "non-fast-forward" in stderr
            or "fetch first" in stderr
            or "rejected" in stderr,
            f"git push failed for a reason that is not a race: {stderr[:300]}",
        )
        require(
            attempt < _PUSH_ATTEMPTS,
            f"the vault branch moved under this gate {_PUSH_ATTEMPTS} times in a "
            "row; another lane is pushing continuously, so pause it rather than "
            "letting this retry forever",
        )
        _git("fetch", "origin", VAULT_BRANCH, cwd=clone)
        _git("rebase", f"origin/{VAULT_BRANCH}", cwd=clone)


async def _await_sentinel(
    scout: _Scout,
    sentinel: str,
    budget: float,
    *,
    poll_seconds: float = PROPAGATION_POLL_SECONDS,
) -> str | None:
    """Poll the served surface for the sentinel, running no other command.

    Running anything else here -- an ingest, a compose restart, a manual sync --
    would make the result say nothing about whether the loop closes on its own.
    """
    deadline = time.monotonic() + budget
    while True:
        for hit in await scout.search(sentinel, k=5):
            path = str(hit.get("path", ""))
            if sentinel in path or sentinel in str(hit.get("snippet", "")):
                return path
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(poll_seconds, remaining))


async def _await_sentinel_absent(
    scout: _Scout,
    sentinel: str,
    budget: float,
    *,
    poll_seconds: float,
) -> float | None:
    """Return seconds to two consecutive misses after a cleanup push."""
    started = time.monotonic()
    deadline = started + budget
    misses = 0
    while True:
        found = await _await_sentinel(scout, sentinel, 0.0)
        misses = misses + 1 if found is None else 0
        if misses >= 2:
            return time.monotonic() - started
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(poll_seconds, remaining))


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
# I-3 latency distribution and correlated stage breakdown
# --------------------------------------------------------------------------


_I3_REQUIRED_STAGES = (
    "watcher_wake",
    "chunk_complete",
    "embed_request_sent",
    "embed_response_received",
    "postgres_commit",
    "row_visible",
)
_I3_REQUIRED_METRICS = (
    "cleanup_push_complete_to_absent",
    "git_push",
    "push_start_to_snapshot",
    "push_complete_to_snapshot",
    "push_complete_to_query",
    "snapshot_to_watcher",
    "watcher_to_chunk",
    "chunk_to_embed_request",
    "embed_round_trip",
    "embed_to_commit",
    "commit_to_row_visible",
    "row_visible_to_query_observed",
)
_I3_RESULTS_DOC = (
    REPO_ROOT
    / "docs"
    / "superpowers"
    / "handoffs"
    / "2026-09-08-codex-i3-latency-results.md"
)


def _parse_log_timestamp(value: str) -> float:
    """Parse Docker and structured-log RFC 3339 timestamps as epoch seconds."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return dt.datetime.fromisoformat(normalized).timestamp()


def _docker_logs(container: str, *, since: float) -> str:
    """Read both streams from one container without interpreting log text."""
    completed = subprocess.run(
        [
            "docker",
            "logs",
            "--timestamps",
            "--since",
            str(max(0, math.floor(since) - 1)),
            container,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    require(
        completed.returncode == 0,
        f"docker logs {container} failed: {completed.stderr.strip()[:200]}",
    )
    return "\n".join(part for part in (completed.stdout, completed.stderr) if part)


def _host_publications(logs: str) -> dict[str, list[float]]:
    """Index host-sync publication timestamps by the commit it published."""
    marker = "Published wiki snapshot at commit "
    found: dict[str, list[float]] = {}
    for line in logs.splitlines():
        if marker not in line:
            continue
        stamp = line.split(maxsplit=1)[0]
        commit = line.rsplit(marker, 1)[1].strip().split()[0]
        if len(commit) not in {40, 64}:
            continue
        found.setdefault(commit, []).append(_parse_log_timestamp(stamp))
    return found


def _sync_stage_records(logs: str) -> dict[str, list[dict[str, object]]]:
    """Index only the bounded sync-job stage records by correlation id."""
    marker = "[sync-job] {"
    found: dict[str, list[dict[str, object]]] = {}
    for line in logs.splitlines():
        if marker not in line:
            continue
        raw = "{" + line.split(marker, 1)[1]
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("event") != "wiki_sync_stage":
            continue
        correlation_id = record.get("correlation_id")
        observed_at = record.get("observed_at")
        if not isinstance(correlation_id, str) or not isinstance(observed_at, str):
            continue
        record["epoch_seconds"] = _parse_log_timestamp(observed_at)
        found.setdefault(correlation_id, []).append(record)
    return found


def _nearest_rank(values: list[float], quantile: float) -> float:
    """Return a distribution percentile by the explicit nearest-rank method."""
    require(bool(values), "cannot calculate a percentile of no observations")
    rank = max(1, math.ceil(quantile * len(values)))
    return sorted(values)[rank - 1]


def _latency_summary(samples: list[dict[str, object]]) -> dict[str, object]:
    """Summarize every complete duration without hiding missing samples."""
    duration_names = sorted(
        {
            name
            for sample in samples
            for name in dict(sample.get("durations_seconds", {}))
        }
    )
    metrics: dict[str, dict[str, float | int]] = {}
    for name in duration_names:
        values = [
            float(dict(sample["durations_seconds"])[name])
            for sample in samples
            if name in dict(sample.get("durations_seconds", {}))
        ]
        metrics[name] = {
            "n": len(values),
            "min": round(min(values), 6),
            "p50": round(_nearest_rank(values, 0.50), 6),
            "p95": round(_nearest_rank(values, 0.95), 6),
            "max": round(max(values), 6),
        }
    return {"method": "nearest-rank", "metrics": metrics}


async def _vault_change_latency() -> str:
    """Measure fresh push-to-query latency and persist its stage distribution."""
    raw_samples = os.environ.get("SNP_PROPAGATION_SAMPLES", "10")
    try:
        sample_count = int(raw_samples)
    except ValueError as exc:
        raise GateFailure(
            f"SNP_PROPAGATION_SAMPLES is not an integer: {raw_samples}"
        ) from exc
    require(sample_count >= 10, f"latency distribution needs N>=10, got {sample_count}")

    raw_poll = os.environ.get("SNP_PROPAGATION_POLL_SECONDS", str(LATENCY_POLL_SECONDS))
    try:
        poll_seconds = float(raw_poll)
    except ValueError as exc:
        raise GateFailure(
            f"SNP_PROPAGATION_POLL_SECONDS is not numeric: {raw_poll}"
        ) from exc
    require(
        0.05 <= poll_seconds <= 1.0,
        f"latency poll must be 0.05..1.0s, got {poll_seconds}",
    )

    report_path = Path(
        os.environ.get("SNP_I3_LATENCY_REPORT", "/tmp/snp-i3-latency/active.json")
    )
    scout = _Scout()
    clone = Path(tempfile.mkdtemp(prefix="v3-vault-latency-"))
    run_started_at = time.time()
    samples: list[dict[str, object]] = []
    live: dict[str, str] = {}
    pending_absence: dict[str, str] = {}
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
        for index in range(sample_count):
            sentinel = f"V3-PROP-LAT-{uuid.uuid4().hex}"
            relative, body = _probe_page(sentinel)
            require(
                await _await_sentinel(scout, sentinel, 0.0) is None,
                f"sample {index + 1}: {sentinel} existed before its push",
            )
            published = _publish(
                clone,
                relative,
                body,
                f"test(w2): latency probe {sentinel}",
            )
            live[sentinel] = relative
            found = await _await_sentinel(
                scout,
                sentinel,
                PROPAGATION_TIMEOUT_SECONDS,
                poll_seconds=poll_seconds,
            )
            query_visible_at = time.time()
            require(
                found is not None,
                f"sample {index + 1}: {sentinel} did not become queryable",
            )
            page = await scout.read(str(found))
            require(
                sentinel in json.dumps(page, ensure_ascii=False),
                f"sample {index + 1}: search found {found}, but read omitted sentinel",
            )

            cleanup = _publish(
                clone,
                relative,
                None,
                f"test(w2): remove latency probe {sentinel}",
            )
            # The page is no longer live in Git once its deletion push returns,
            # but it can remain queryable until the watcher handles that commit.
            # Track those two states separately so an exception during the wait
            # cannot make the finalizer try to create a second deletion commit.
            live.pop(sentinel)
            pending_absence[sentinel] = relative
            cleanup_seconds = await _await_sentinel_absent(
                scout,
                sentinel,
                PROPAGATION_TIMEOUT_SECONDS,
                poll_seconds=poll_seconds,
            )
            require(
                cleanup_seconds is not None,
                f"sample {index + 1}: cleanup {cleanup.commit} stayed queryable",
            )
            pending_absence.pop(sentinel)
            samples.append(
                {
                    "commit": published.commit,
                    "cleanup_commit": cleanup.commit,
                    "cleanup_seconds": round(cleanup_seconds, 6),
                    "index": index + 1,
                    "path": relative,
                    "push_completed_at": published.push_completed_at,
                    "push_started_at": published.push_started_at,
                    "query_visible_at": query_visible_at,
                    "sentinel": sentinel,
                }
            )
    finally:
        # A failed measurement must not leave its page in Git *or* in the
        # served corpus. Cleanup failure is elevated above the measurement
        # failure because a live probe contaminates the system agents query.
        cleanup_failures: list[str] = []
        for sentinel, relative in list(live.items()):
            try:
                _publish(
                    clone,
                    relative,
                    None,
                    f"test(w2): remove failed latency probe {sentinel}",
                )
                live.pop(sentinel)
                pending_absence[sentinel] = relative
            except Exception as exc:  # noqa: BLE001 - report every cleanup fault
                cleanup_failures.append(
                    f"{sentinel}: deletion push failed: {type(exc).__name__}: {exc}"
                )

        for sentinel in list(pending_absence):
            try:
                absent_after = await _await_sentinel_absent(
                    scout,
                    sentinel,
                    PROPAGATION_TIMEOUT_SECONDS,
                    poll_seconds=poll_seconds,
                )
                if absent_after is None:
                    cleanup_failures.append(
                        f"{sentinel}: deletion was pushed but remained queryable"
                    )
                else:
                    pending_absence.pop(sentinel)
            except Exception as exc:  # noqa: BLE001 - report every cleanup fault
                cleanup_failures.append(
                    f"{sentinel}: absence check failed: {type(exc).__name__}: {exc}"
                )
        shutil.rmtree(clone, ignore_errors=True)
        require(
            not cleanup_failures and not live and not pending_absence,
            "latency probe cleanup incomplete: " + "; ".join(cleanup_failures),
        )

    host_by_commit = _host_publications(
        _docker_logs("snp-memory-host-sync-1", since=run_started_at)
    )
    sync_by_commit = _sync_stage_records(
        _docker_logs("snp-memory-sync-job-1", since=run_started_at)
    )

    for sample in samples:
        commit = str(sample["commit"])
        relative = str(sample["path"])
        publications = host_by_commit.get(commit, [])
        require(publications, f"{commit}: no host-sync publication record")
        published_at = min(publications)
        records = sync_by_commit.get(commit, [])
        require(records, f"{commit}: no correlated sync-job stage records")

        stage_times: dict[str, float] = {}
        for stage in _I3_REQUIRED_STAGES:
            matches = [
                record
                for record in records
                if record.get("stage") == stage
                and (stage == "watcher_wake" or record.get("source_uri") == relative)
            ]
            require(matches, f"{commit}: stage {stage!r} was not logged for {relative}")
            stage_times[stage] = min(
                float(record["epoch_seconds"]) for record in matches
            )

        ordered = [stage_times[stage] for stage in _I3_REQUIRED_STAGES]
        require(
            ordered == sorted(ordered),
            f"{commit}: sync stages are out of order: {stage_times}",
        )
        query_visible_at = float(sample["query_visible_at"])
        push_started_at = float(sample["push_started_at"])
        push_completed_at = float(sample["push_completed_at"])
        durations = {
            "cleanup_push_complete_to_absent": float(sample["cleanup_seconds"]),
            "git_push": push_completed_at - push_started_at,
            "push_start_to_snapshot": published_at - push_started_at,
            "push_complete_to_snapshot": published_at - push_completed_at,
            "push_complete_to_query": query_visible_at - push_completed_at,
            "snapshot_to_watcher": stage_times["watcher_wake"] - published_at,
            "watcher_to_chunk": stage_times["chunk_complete"]
            - stage_times["watcher_wake"],
            "chunk_to_embed_request": stage_times["embed_request_sent"]
            - stage_times["chunk_complete"],
            "embed_round_trip": stage_times["embed_response_received"]
            - stage_times["embed_request_sent"],
            "embed_to_commit": stage_times["postgres_commit"]
            - stage_times["embed_response_received"],
            "commit_to_row_visible": stage_times["row_visible"]
            - stage_times["postgres_commit"],
            "row_visible_to_query_observed": query_visible_at
            - stage_times["row_visible"],
        }
        require(
            all(value >= -0.01 for value in durations.values()),
            f"{commit}: clocks produced an invalid negative stage: {durations}",
        )
        sample["host_snapshot_published_at"] = published_at
        sample["stage_epoch_seconds"] = stage_times
        sample["durations_seconds"] = {
            name: round(value, 6) for name, value in durations.items()
        }

    summary = _latency_summary(samples)
    report = {
        "measured_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "poll_seconds": poll_seconds,
        "sample_count": len(samples),
        "samples": samples,
        "schema_version": 1,
        "summary": summary,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(f"{report_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(report_path)

    metrics = dict(summary["metrics"])
    end_to_end = dict(metrics["push_complete_to_query"])
    print(
        f"  N={len(samples)} push-complete-to-query "
        f"p50={float(end_to_end['p50']):.3f}s "
        f"p95={float(end_to_end['p95']):.3f}s; "
        f"report={report_path}"
    )
    return "VAULT CHANGE LATENCY MEASURED"


def group_vault_change_latency() -> str:
    """Ten or more live single-page pushes produce a latency distribution."""
    return asyncio.run(_vault_change_latency())


def group_i3_latency_report() -> str:
    """The durable handoff agrees with the raw measurement and preserves I-3."""
    report_path = Path(
        os.environ.get("SNP_I3_LATENCY_REPORT", "/tmp/snp-i3-latency/active.json")
    )
    require(report_path.is_file(), f"latency report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    require(isinstance(report, dict), "latency report is not an object")
    sample_count = int(report.get("sample_count", 0))
    require(sample_count >= 10, f"latency report has only {sample_count} samples")
    samples = report.get("samples", [])
    require(
        isinstance(samples, list) and len(samples) == sample_count,
        "latency report sample rows are incomplete",
    )
    summary = dict(report.get("summary", {}))
    require(summary.get("method") == "nearest-rank", "unexpected percentile method")
    metrics = dict(summary.get("metrics", {}))
    for name in _I3_REQUIRED_METRICS:
        metric = dict(metrics.get(name, {}))
        require(
            int(metric.get("n", 0)) == sample_count,
            f"latency metric {name!r} is incomplete",
        )
        ordered = [float(metric[key]) for key in ("min", "p50", "p95", "max")]
        require(
            all(math.isfinite(value) and value >= 0 for value in ordered)
            and ordered == sorted(ordered),
            f"latency metric {name!r} has invalid bounds: {ordered}",
        )
    end_to_end = dict(metrics.get("push_complete_to_query", {}))
    require(
        all(
            isinstance(sample, dict)
            and isinstance(sample.get("cleanup_commit"), str)
            and float(sample.get("cleanup_seconds", -1)) >= 0
            for sample in samples
        ),
        "one or more samples lack confirmed cleanup evidence",
    )

    require(_I3_RESULTS_DOC.is_file(), f"written findings missing: {_I3_RESULTS_DOC}")
    findings = _I3_RESULTS_DOC.read_text(encoding="utf-8")
    required_text = (
        f"Samples: **{sample_count}**",
        f"p50: **{float(end_to_end['p50']):.3f} s**",
        f"p95: **{float(end_to_end['p95']):.3f} s**",
        "Option A — sparse checkout",
        "Option B — manual sync script",
        "Option C — separate demo checkout",
        "Option D — sparse checkout plus Obsidian Git",
        "OWNER DECISION REQUIRED",
        "I-3 remains unchanged",
        "Obsidian-to-push is not measured",
    )
    missing = [text for text in required_text if text not in findings]
    require(not missing, f"written findings omit: {missing}")

    demo = (REPO_ROOT / "docs" / "DEMO_OPENCODE.md").read_text(encoding="utf-8")
    require(
        "Human edit in Obsidian updates index in < 5s." in demo,
        "the unapproved I-3 criterion was changed",
    )
    require(
        "I-3 is currently expected to fail as written" in demo,
        "the runbook no longer states the known I-3 failure",
    )
    print(f"  findings match N={sample_count} raw samples; I-3 remains unchanged")
    return "I3 LATENCY REPORT VERIFIED"


def group_i3_commit_scope() -> str:
    """The final commit excludes private and unrelated working-tree paths."""
    allowed = {
        "CLAUDE.md",
        "artifacts/v3/checks/engine_acceptance.py",
        "docs/superpowers/handoffs/2026-09-08-codex-i3-latency-results.md",
        "scout/ingest.py",
        "scout/sync_job.py",
        "scout/wiki_ingest.py",
        "tests/test_engine_acceptance_publish.py",
        "tests/test_ingest_v2.py",
        "tests/test_sync_job.py",
        "tests/test_wiki_ingest.py",
    }
    changed = {
        line
        for line in _git(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "HEAD",
            cwd=REPO_ROOT,
        ).stdout.splitlines()
        if line
    }
    require(bool(changed), "HEAD contains no paths to scope-check")
    unexpected = sorted(changed - allowed)
    require(not unexpected, f"I-3 commit includes undeclared paths: {unexpected}")
    require(
        "docs/DEMO_OPENCODE.md" not in changed,
        "the owner-controlled I-3 criterion changed before approval",
    )
    print(f"  {len(changed)} committed path(s), all within the I-3 ownership set")
    return "I3 COMMIT SCOPE VERIFIED"


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
    # Gitea's job status enum, from modules/actions: 0 unknown, 1 success,
    # 2 failure, 3 cancelled, 4 skipped, 5 waiting, 6 running. Only success and
    # failure mean the job ran to a verdict, which is what "gated something"
    # means. This gate previously treated 3 and 4 as verdicts, which is exactly
    # backwards -- it would have credited a cancelled job as a passing CI run,
    # and 44 of the jobs on this instance are cancelled.
    reached_verdict = {"1", "2"}
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
    "vault-change-latency": group_vault_change_latency,
    "i3-latency-report": group_i3_latency_report,
    "i3-commit-scope": group_i3_commit_scope,
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
