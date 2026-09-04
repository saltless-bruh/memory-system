#!/usr/bin/env python3
"""Oracles for the scout container rebuild (R1-R5).

Each ``--group`` measures the running stack directly and prints a success-only
token after every assertion in that group has passed. There is no path through
any group that prints a token while skipping its measurement: a subject that
cannot be found fails the gate rather than excusing it.

Usage:
    .venv/bin/python artifacts/v3/checks/rebuild.py --group <name>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGE = "snp-scout"
CONTAINER = "snp-memory-scout-1"

#: Directories the Dockerfile copies into the image, mapped to their in-image
#: destination. Checking the whole tree rather than a sample is the point:
#: leaf-1.5.1's oracle hashes two files, which cannot distinguish a partial
#: `docker cp` from a real build.
PACKAGED_TREES = {
    "scout": "/app/scout",
    "scripts": "/app/scripts",
}


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def _docker(*args: str, timeout: int = 120) -> str:
    """Run one docker command, failing the gate on any non-zero exit."""
    proc = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    require(
        proc.returncode == 0,
        f"docker {' '.join(args[:3])} failed ({proc.returncode}): "
        f"{proc.stderr.strip()[:300]}",
    )
    return proc.stdout


def _inspect(ref: str, fmt: str) -> str:
    return _docker("inspect", ref, "--format", fmt).strip()


def _newest_source_mtime() -> datetime:
    """The most recent edit across every tree the image packages."""
    newest = 0.0
    for tree in PACKAGED_TREES:
        for path in (REPO_ROOT / tree).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            newest = max(newest, path.stat().st_mtime)
    require(newest > 0, "found no packaged Python source to date the build against")
    return datetime.fromtimestamp(newest, tz=UTC)


def _container_hashes() -> dict[str, str]:
    """SHA-256 of every packaged .py file, keyed by repo-relative path.

    Hashing happens inside the container in one exec, so the measurement is of
    what the image actually holds rather than of what a copy of it holds.
    """
    probe = (
        "import hashlib,os,json;"
        "out={};"
        f"trees={json.dumps(PACKAGED_TREES)};"
        "\nfor tree,dest in trees.items():\n"
        "    for root,dirs,files in os.walk(dest):\n"
        "        dirs[:]=[d for d in dirs if d!='__pycache__']\n"
        "        for name in files:\n"
        "            if not name.endswith('.py'): continue\n"
        "            full=os.path.join(root,name)\n"
        "            rel=tree+full[len(dest):]\n"
        "            with open(full,'rb') as fh:\n"
        "                out[rel]=hashlib.sha256(fh.read()).hexdigest()\n"
        "print(json.dumps(out))"
    )
    raw = _docker("exec", CONTAINER, "python", "-c", probe)
    parsed: dict[str, str] = json.loads(raw)
    return parsed


def _working_tree_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    for tree in PACKAGED_TREES:
        for path in (REPO_ROOT / tree).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def group_image_freshness() -> str:
    """The image was rebuilt after the last source edit, not replayed from cache.

    A `docker compose build` that hits cache for every layer exits zero and
    leaves the old image in place, so exit status proves nothing. The image's
    creation timestamp must be later than the newest file it is supposed to
    contain.
    """
    created_raw = _inspect(IMAGE, "{{.Created}}")
    created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    newest_source = _newest_source_mtime()

    require(
        created > newest_source,
        f"image {IMAGE} was created {created.astimezone(UTC).isoformat()} but the newest "
        f"packaged source was edited {newest_source.isoformat()}; the build "
        "replayed a cached layer and the image predates the code",
    )

    # A build that changed nothing would leave the running container's image id
    # equal to a stale one; recording the id here is what R3 checks against.
    image_id = _inspect(IMAGE, "{{.Id}}")
    require(
        image_id.startswith("sha256:"),
        f"unexpected image id for {IMAGE}: {image_id!r}",
    )
    return "IMAGE FRESHNESS VERIFIED"


def group_source_identity() -> str:
    """Every packaged Python file in the container matches the working tree.

    Byte identity over the whole tree, with a planted control. The control
    matters because a hash comparison that silently comes up empty -- a wrong
    path, a failed exec, an image whose layout changed -- would report perfect
    agreement over zero files.
    """
    in_container = _container_hashes()
    on_disk = _working_tree_hashes()

    require(
        len(in_container) > 50,
        f"only {len(in_container)} packaged .py files found in {CONTAINER}; "
        "the probe cannot be measuring the real image",
    )

    missing = sorted(set(on_disk) - set(in_container))
    require(
        not missing,
        f"{len(missing)} working-tree files are absent from the image, "
        f"starting with {missing[:5]}",
    )

    differing = sorted(
        rel for rel, digest in on_disk.items() if in_container.get(rel) != digest
    )
    require(
        not differing,
        f"{len(differing)} packaged files differ between the image and the "
        f"working tree, starting with {differing[:5]}",
    )

    # Positive control, run through the same comparison expression the real
    # check uses rather than a separate hash inequality. An all-equal result
    # never demonstrates that the comparison can see a difference; planting one
    # and re-running the identical generator does.
    control_rel = "scout/backends/pgvector.py"
    require(control_rel in on_disk, f"control file {control_rel} is not packaged")
    planted = dict(in_container)
    planted[control_rel] = hashlib.sha256(b"planted control").hexdigest()
    control_differing = sorted(
        rel for rel, digest in on_disk.items() if planted.get(rel) != digest
    )
    require(
        control_differing == [control_rel],
        "the comparison did not flag a planted mismatch in "
        f"{control_rel}; it reported {control_differing}. A hash check that "
        "cannot detect a difference proves nothing when it finds none",
    )

    return "SOURCE IDENTITY VERIFIED"


def group_runtime_state() -> str:
    """The container actually runs the new image, is healthy, and keeps its mount.

    Building an image changes nothing until something restarts onto it. The
    read-only vault mount is checked here because `wiki_read` serves page
    bodies from it, and a compose edit that dropped it would leave retrieval
    working and reads broken.
    """
    image_id = _inspect(IMAGE, "{{.Id}}")
    running_image = _inspect(CONTAINER, "{{.Image}}")
    require(
        running_image == image_id,
        f"{CONTAINER} is running image {running_image[:19]} but {IMAGE} is now "
        f"{image_id[:19]}; the container was not restarted onto the rebuild",
    )

    status = _inspect(CONTAINER, "{{.State.Status}}")
    require(status == "running", f"{CONTAINER} is {status!r}, not running")

    health = _inspect(CONTAINER, "{{if .State.Health}}{{.State.Health.Status}}{{end}}")
    require(
        health == "healthy",
        f"{CONTAINER} health is {health!r}; the rebuilt image does not come up",
    )

    mounts_raw = _inspect(CONTAINER, "{{json .Mounts}}")
    mounts = json.loads(mounts_raw)
    vault = [m for m in mounts if m.get("Destination") == "/vault-replica"]
    require(
        len(vault) == 1,
        "the read-only vault-replica mount is not present; wiki_read reads page "
        f"bodies from it. Mounts: {[m.get('Destination') for m in mounts]}",
    )
    require(
        vault[0].get("RW") is False,
        "the vault-replica mount is writable; INV-1 requires nothing write back "
        "up the chain",
    )

    return "RUNTIME STATE VERIFIED"


def group_revision_stamp() -> str:
    """The image says what it was built from, rather than carrying the default.

    `scout/Dockerfile` defaults `SNP_GIT_REVISION` to the literal `unknown`, so
    a build that never passes the arg produces an image whose provenance label
    is indistinguishable from an export-only checkout's. That is the state the
    stamp exists to make impossible, and it is what this rebuild found.
    """
    revision = _inspect(
        IMAGE, '{{index .Config.Labels "org.opencontainers.image.revision"}}'
    )
    require(
        bool(revision) and revision != "unknown",
        "the image still carries the default revision label 'unknown', so a "
        "stale image stays indistinguishable from a fresh one",
    )

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    require(bool(head), "could not read git HEAD to compare the revision against")
    require(
        revision.startswith(head),
        f"the image revision {revision!r} does not begin with HEAD {head[:7]}; "
        "it was built from a different commit than this checkout",
    )

    # A dirty tree cannot be reproduced from any commit, so the stamp must say
    # so rather than name a commit the image does not actually match.
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if dirty:
        require(
            revision.endswith("-dirty"),
            f"the working tree has uncommitted changes but the image is stamped "
            f"{revision!r}, which claims it is reproducible from {head[:7]}",
        )

    return "REVISION STAMP VERIFIED"


def group_served_corpus_tier() -> str:
    """leaf-1.5.4's corpus tier is live inside the container that serves it.

    Measured through the container's own production wiring and its own database
    connection, not through the host's. Two halves, both needed: the served
    surface must build a wiki-tiered backend, and that backend must actually
    hide raw evidence. The second half carries its own positive control -- an
    unfiltered backend built in the same process must reach the raw document,
    or the absence proves nothing.
    """
    probe = r"""
import asyncio, json, sys
from scout.serve import _build_production_backend
from scout.backends.pgvector import PgVectorRlsBackend
from scout.policy import CANONICAL_DEPARTMENTS
from scout.types import Scope

served = _build_production_backend("pgvector")
result = {"served_corpus": getattr(served, "corpus", "<absent>")}

async def main():
    scope = Scope(departments=frozenset(CANONICAL_DEPARTMENTS))
    unfiltered = PgVectorRlsBackend()
    async with (await unfiltered._get_pool()).acquire() as conn:
        await conn.execute(
            "SELECT set_config('scout.current_depts', $1, false);",
            ",".join(sorted(CANONICAL_DEPARTMENTS)),
        )
        row = await conn.fetchrow(
            "SELECT d.source_uri, c.chunk_text FROM rag_chunks c "
            "JOIN rag_documents d ON d.doc_id = c.doc_id "
            "WHERE c.metadata->>'corpus' IS NULL "
            "ORDER BY length(c.chunk_text) DESC LIMIT 1"
        )
    if row is None:
        result["raw_uri"] = None
        return
    result["raw_uri"] = row["source_uri"]
    hint = " ".join(row["chunk_text"].split())[:200]
    control = await unfiltered.retrieve(hint, scope=scope, k=10)
    result["control_paths"] = sorted({c.file_path for c in control})
    tiered = await served.retrieve(hint, scope=scope, k=10)
    result["served_paths"] = sorted({c.file_path for c in tiered})
    await unfiltered.close()
    await served.close()

asyncio.run(main())
print(json.dumps(result))
"""
    raw = _docker("exec", CONTAINER, "python", "-c", probe, timeout=180)
    measured = json.loads(raw.strip().splitlines()[-1])

    require(
        measured["served_corpus"] == "wiki",
        "the container's own production wiring builds a backend with corpus="
        f"{measured['served_corpus']!r}; the served surface is not on the wiki tier",
    )

    raw_uri = measured.get("raw_uri")
    require(
        raw_uri is not None,
        "no raw-corpus document in the index the container reads, so the tier "
        "has nothing to hide and this gate cannot prove anything",
    )
    require(
        raw_uri in measured.get("control_paths", []),
        f"positive control failed inside the container: an unfiltered backend "
        f"did not reach {raw_uri}, so its absence downstream proves nothing. "
        f"Control returned {measured.get('control_paths')}",
    )
    require(
        len(measured.get("served_paths", [])) > 0,
        "the served backend returned nothing at all; an exclusion that hides "
        "the whole corpus is a broken filter, not a tier",
    )
    require(
        raw_uri not in measured["served_paths"],
        f"the served surface inside the container surfaced {raw_uri}",
    )

    return "SERVED CORPUS TIER VERIFIED"


GROUPS: dict[str, Callable[[], str]] = {
    "image-freshness": group_image_freshness,
    "source-identity": group_source_identity,
    "runtime-state": group_runtime_state,
    "revision-stamp": group_revision_stamp,
    "served-corpus-tier": group_served_corpus_tier,
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
