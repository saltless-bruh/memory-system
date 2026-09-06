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
VAULT = Path(
    os.environ.get("SNP_REFERENCE_VAULT")
    or Path.home() / "Documents" / "memo-project" / "Obsidian Vault"
)

#: Read from constants, never from the measured result. A floor derived from
#: what was observed is not a floor.
RECALL_AT_1_FLOOR = 0.60
RECALL_AT_5_FLOOR = 0.85
SEARCH_K = 5


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


GROUPS: dict[str, Callable[[], str]] = {
    "find-read-cite": group_find_read_cite,
    "vault-change-propagates": group_vault_change_propagates,
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
