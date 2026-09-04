#!/usr/bin/env python3
"""Oracles for seeding the lead's vault as the served corpus (V1-V5).

The deliverable is not "files were pushed". It is that a page `wiki_search`
finds is a page `wiki_read` returns, measured through the container that serves
them and over the whole index rather than a spot check.

Usage:
    .venv/bin/python artifacts/v3/checks/vault_seed.py --group <name>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTAINER = "snp-memory-scout-1"
#: The scout image ships no git; host-sync owns the repository clone and is
#: the only container that can answer questions about published history.
SYNC_CONTAINER = "snp-memory-host-sync-1"
GITEA_REMOTE = "http://127.0.0.1:3000/snp-admin/snp-memory.git"

#: The commit Gitea main carried before the seed. The replaced sample pages
#: must stay reachable there; a force-push that orphaned them would be a
#: silent loss of the compile pipeline's reference corpus.
PRE_SEED_COMMIT = "26abe20e77d54eee8416aeaeeb90d57ae47f8b99"

#: Measured failing before the seed: wiki_search returned these five and
#: wiki_read raised KeyError on every one.
KNOWN_FAILURES_QUERY = "headless browser library for controlling Chrome"


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def _run(args: list[str], *, timeout: int = 180, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        args, capture_output=True, text=True, check=False, timeout=timeout, cwd=cwd
    )
    require(
        proc.returncode == 0,
        f"{' '.join(args[:4])} failed ({proc.returncode}): {proc.stderr.strip()[:400]}",
    )
    return proc.stdout


def _in_container(
    script: str, *, timeout: int = 600, container: str = CONTAINER
) -> dict[str, Any]:
    """Run a probe inside a stack container and parse its JSON last line."""
    out = _run(["docker", "exec", container, "python", "-c", script], timeout=timeout)
    lines = [line for line in out.strip().splitlines() if line.strip()]
    require(bool(lines), "the container probe produced no output")
    parsed: dict[str, Any] = json.loads(lines[-1])
    return parsed


def group_published() -> str:
    """Gitea main carries the corpus, and the authored control docs survived.

    `index.md` is the one file a careless seed destroys: `gen_index.py` would
    rewrite it from `summary:` frontmatter and emit blank descriptions over the
    authored ones. Counting them here is what makes that loss visible.
    """
    head = _run(["git", "ls-remote", GITEA_REMOTE, "refs/heads/main"]).split()
    require(len(head) >= 1, "could not read Gitea main")
    published = head[0]
    require(
        published != PRE_SEED_COMMIT,
        f"Gitea main is still at the pre-seed commit {PRE_SEED_COMMIT[:7]}",
    )

    # Reading the published tree through the replica the service already
    # cloned, rather than fetching a second copy just to count files.
    probe = (
        "import json,os;"
        "root='/vault-replica/current/wiki';"
        "md=[os.path.join(r,f) for r,d,fs in os.walk(root) for f in fs "
        "if f.endswith('.md')];"
        "idx=open(os.path.join(root,'index.md'),encoding='utf-8').read();"
        "desc=sum(1 for line in idx.splitlines() "
        "if line.lstrip().startswith(('- [','* [')));"
        "print(json.dumps({'pages':len(md),"
        "'has_log':os.path.exists(os.path.join(root,'log.md')),"
        "'descriptions':desc}))"
    )
    measured = _in_container(probe)

    pages = int(measured["pages"])
    require(
        pages > 400,
        f"the published corpus holds {pages} pages; the lead's vault is 433 and "
        "the pre-seed sample was 8, so this is not the seeded corpus",
    )
    require(bool(measured["has_log"]), "log.md did not survive the replacement")
    descriptions = int(measured["descriptions"])
    require(
        descriptions > 300,
        f"index.md carries {descriptions} authored descriptions; the vault's "
        "index had 306 and the pre-seed sample had 7, so the authored index "
        "was overwritten rather than carried across",
    )
    return "VAULT PUBLISHED VERIFIED"


def group_replicated() -> str:
    """host-sync published that exact commit into the volume the container reads.

    Checked against the service's own readiness report, not against the files
    alone: a replica that happens to hold the right pages while the service is
    `degraded` will not survive the next push, and the demo depends on the next
    push working.
    """
    import urllib.request

    with urllib.request.urlopen("http://127.0.0.1:9000/ready", timeout=15) as resp:
        state = json.loads(resp.read().decode("utf-8"))

    require(
        state.get("last_error") is None,
        f"host-sync reports last_error={state.get('last_error')!r}; replication "
        "is not healthy even if the current files look right",
    )
    require(
        state.get("status") == "ready",
        f"host-sync status is {state.get('status')!r}, not ready",
    )
    published = str(state.get("published_commit") or "")
    require(
        published != PRE_SEED_COMMIT,
        "host-sync is still publishing the pre-seed commit",
    )

    remote_head = _run(["git", "ls-remote", GITEA_REMOTE, "refs/heads/main"]).split()[0]
    require(
        published == remote_head,
        f"host-sync published {published[:7]} but Gitea main is at "
        f"{remote_head[:7]}; the replica is behind",
    )
    return "VAULT REPLICATED VERIFIED"


def group_read_coverage() -> str:
    """Substantially every indexed page resolves through the served engine.

    A rate over the whole index, because a spot check cannot distinguish a
    corpus that mostly works from one that works for the five pages someone
    happened to try. The index is the authority for what must be readable:
    every document `wiki_search` can return is a document `wiki_read` has to
    serve, or the system finds what it cannot answer.
    """
    probe = r"""
import asyncio, json
from scout.mcp_server import _default_engine
from scout.backends.pgvector import PgVectorRlsBackend
from scout.policy import CANONICAL_DEPARTMENTS
from scout.types import Scope

backend = PgVectorRlsBackend(corpus="wiki")
engine = _default_engine(backend)
scope = Scope(departments=frozenset(CANONICAL_DEPARTMENTS))

async def main():
    async with (await backend._get_pool()).acquire() as conn:
        await conn.execute(
            "SELECT set_config('scout.current_depts', $1, false);",
            ",".join(sorted(CANONICAL_DEPARTMENTS)),
        )
        rows = await conn.fetch(
            "SELECT DISTINCT d.source_uri FROM rag_documents d "
            "JOIN rag_chunks c ON c.doc_id = d.doc_id "
            "WHERE c.metadata->>'corpus' = 'wiki'"
        )
    uris = [r["source_uri"] for r in rows]
    ok, failed = 0, []
    for uri in uris:
        try:
            await engine.wiki_read(uri, mode="tldr", scope=scope)
            ok += 1
        except Exception:
            failed.append(uri)
    await backend.close()
    print(json.dumps({"indexed": len(uris), "readable": ok,
                      "unreadable": failed[:10], "unreadable_n": len(failed)}))

asyncio.run(main())
"""
    measured = _in_container(probe, timeout=900)
    indexed = int(measured["indexed"])
    readable = int(measured["readable"])

    require(
        indexed > 400,
        f"only {indexed} wiki documents in the index; this is not the lead's "
        "corpus and the coverage rate would be meaningless",
    )
    rate = readable / indexed
    require(
        rate >= 0.99,
        f"only {readable} of {indexed} indexed pages ({rate:.1%}) can be read "
        f"through the served engine. Unreadable sample: "
        f"{measured['unreadable']}",
    )
    return "READ COVERAGE VERIFIED"


def group_known_failures() -> str:
    """The five pages that raised KeyError now return bodies, and a missing page still fails.

    The negative half is the control. Without it this gate cannot tell a fixed
    read path from one that has started inventing pages, which is the worse
    failure of the two.
    """
    probe = r"""
import asyncio, json
from scout.mcp_server import _default_engine
from scout.backends.pgvector import PgVectorRlsBackend
from scout.policy import CANONICAL_DEPARTMENTS
from scout.types import Scope

backend = PgVectorRlsBackend(corpus="wiki")
engine = _default_engine(backend)
scope = Scope(departments=frozenset(CANONICAL_DEPARTMENTS))
QUERY = __QUERY__

async def main():
    hits = await engine.wiki_search(QUERY, k=5, scope=scope)
    result = {"found": [h.path for h in hits], "read_ok": [], "read_fail": [],
              "bodies_nonempty": 0}
    for h in hits:
        try:
            page = await engine.wiki_read(h.path, mode="full", scope=scope)
            result["read_ok"].append(h.path)
            if (page.body or "").strip():
                result["bodies_nonempty"] += 1
        except Exception as exc:
            result["read_fail"].append(f"{h.path}:{type(exc).__name__}")
    try:
        await engine.wiki_read("Entities/__no_such_page_control__.md",
                               mode="tldr", scope=scope)
        result["absent_page_raised"] = False
    except Exception:
        result["absent_page_raised"] = True
    await backend.close()
    print(json.dumps(result))

asyncio.run(main())
""".replace("__QUERY__", repr(KNOWN_FAILURES_QUERY))
    measured = _in_container(probe)

    found = list(measured["found"])
    require(
        len(found) == 5,
        f"wiki_search returned {len(found)} pages, not the 5 this gate tracks",
    )
    fails = list(measured["read_fail"])
    require(
        not fails,
        f"wiki_read still fails on {fails}; these are the exact pages the seed "
        "was performed to fix",
    )
    nonempty = int(measured["bodies_nonempty"])
    require(
        nonempty == len(found),
        f"only {nonempty} of {len(found)} reads returned a non-empty body; a "
        "read that succeeds and returns nothing is not an answer",
    )
    require(
        bool(measured["absent_page_raised"]),
        "reading a page that does not exist did NOT raise; the read path has "
        "stopped distinguishing present from absent, which is worse than the "
        "KeyError this seed fixed",
    )
    return "KNOWN FAILURES FIXED"


def group_blast_radius() -> str:
    """Only wiki/ changed, and the replaced sample pages remain in history."""
    published = _run(["git", "ls-remote", GITEA_REMOTE, "refs/heads/main"]).split()[0]
    probe = (
        "import json,subprocess;"
        "r='/vault-replica/repository';"
        "names=subprocess.run(['git','-c','core.quotePath=false','-C',r,"
        "'diff','--name-only',"
        f"'{PRE_SEED_COMMIT}','{published}'],capture_output=True,text=True);"
        "anc=subprocess.run(['git','-C',r,'merge-base','--is-ancestor',"
        f"'{PRE_SEED_COMMIT}','{published}'],capture_output=True,text=True);"
        "print(json.dumps({'names':[n for n in names.stdout.splitlines() if n],"
        "'rc':names.returncode,'ancestor_rc':anc.returncode}))"
    )
    measured = _in_container(probe, container=SYNC_CONTAINER)
    require(
        int(measured["rc"]) == 0,
        "could not diff the pre-seed commit against the published one",
    )
    names = [str(n) for n in measured["names"]]
    require(bool(names), "the published commit changed nothing at all")
    outside = [n for n in names if not n.startswith("wiki/")]
    require(
        not outside,
        f"the seed changed {len(outside)} paths outside wiki/: {outside[:8]}",
    )
    require(
        int(measured["ancestor_rc"]) == 0,
        f"{PRE_SEED_COMMIT[:7]} is no longer an ancestor of main; the seed "
        "rewrote history and the replaced sample pages are unrecoverable",
    )
    return "SEED BLAST RADIUS VERIFIED"


GROUPS: dict[str, Callable[[], str]] = {
    "published": group_published,
    "replicated": group_replicated,
    "read-coverage": group_read_coverage,
    "known-failures": group_known_failures,
    "blast-radius": group_blast_radius,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
