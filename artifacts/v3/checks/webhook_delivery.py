#!/usr/bin/env python3
"""Oracles for W-2 webhook delivery and for committing the branch (W1-W3, C1-C4).

Usage:
    .venv/bin/python artifacts/v3/checks/webhook_delivery.py --group <name>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
GIT_CONTAINER = "snp-memory-git-1"
SYNC_CONTAINER = "snp-memory-host-sync-1"
SYNC_READY = "http://127.0.0.1:9000/ready"
GITEA_REMOTE = "http://127.0.0.1:3000/snp-admin/snp-memory.git"
FEATURE_BRANCH = "feat/v3-retrieval-inversion"

#: Paths deliberately left out of the branch. Every entry must be editor state,
#: throwaway scratch, or an unreferenced binary -- never source, test, ledger or
#: configuration. C1 enforces that classification rather than trusting the list.
EXPECTED_EXCLUSIONS = {
    # Already tracked Obsidian UI state. Left modified rather than committed
    # or reverted: committing it puts per-machine editor preferences in the
    # branch, and reverting it would silently change the owner's editor.
    ".obsidian/app.json",
    "Untitled.md",
    "docs/image-conv-with-boss/Screenshot_20260827_215910.png",
    "docs/image-conv-with-boss/Screenshot_20260827_215952.png",
    "docs/image-conv-with-boss/Screenshot_20260827_220010.png",
    "docs/image-conv-with-boss/Screenshot_20260827_220020.png",
    "docs/image-conv-with-boss/Screenshot_20260828_081008.png",
}

#: A path in history matching any of these is a hygiene failure. `.secrets/` and
#: the real `.env` carry live credentials; `.obsidian/` is per-machine UI state.
FORBIDDEN_IN_HISTORY = (".secrets/", ".obsidian/", "Untitled.md")


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def _run(args: list[str], *, timeout: int = 300, check: bool = True) -> str:
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        cwd=REPO_ROOT,
    )
    if check:
        require(
            proc.returncode == 0,
            f"{' '.join(args[:4])} failed ({proc.returncode}): "
            f"{proc.stderr.strip()[:400]}",
        )
    return proc.stdout


def _sync_state() -> dict[str, Any]:
    with urllib.request.urlopen(SYNC_READY, timeout=15) as resp:
        state: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    return state


def _gitea_head() -> str:
    return _run(["git", "ls-remote", GITEA_REMOTE, "refs/heads/main"]).split()[0]


def group_setting_live() -> str:
    """The running Gitea process holds the allowlist.

    Read from the container, not from docker-compose.yml. A value present in
    the compose file and absent from the process is the state this gate exists
    to catch: the file was edited and nothing was restarted, which looks
    identical to a fix in a diff and behaves identically to no fix at all.
    """
    env_value = _run(
        [
            "docker",
            "exec",
            GIT_CONTAINER,
            "sh",
            "-c",
            'printf %s "${GITEA__webhook__ALLOWED_HOST_LIST:-}"',
        ]
    ).strip()
    require(
        env_value != "",
        "the running Gitea container has no GITEA__webhook__ALLOWED_HOST_LIST; "
        "the compose file may be edited but the service was never recreated",
    )
    require(
        "host-sync" in env_value,
        f"the allowlist is {env_value!r} and does not permit host-sync",
    )

    rendered = _run(
        ["docker", "exec", GIT_CONTAINER, "sh", "-c", "cat /data/gitea/conf/app.ini"]
    )
    require(
        "ALLOWED_HOST_LIST" in rendered and "host-sync" in rendered,
        "Gitea's rendered app.ini carries no host-sync allowlist entry, so the "
        "environment variable did not reach the configuration it actually reads",
    )
    require(
        "\n[webhook]" in rendered,
        "app.ini has no [webhook] section; the setting landed in the wrong one",
    )
    return "WEBHOOK ALLOWLIST LIVE"


def _git_container_ip() -> str:
    ip = _run(
        [
            "docker",
            "inspect",
            GIT_CONTAINER,
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        ]
    ).strip()
    require(bool(ip), "could not resolve the git container's address")
    return ip


def group_delivery() -> str:
    """Gitea delivers to host-sync, and no allowlist denial follows.

    The positive half is a POST arriving at host-sync **from the git
    container's own address**. That distinction is the whole gate: a manual
    trigger from the host arrives from the bridge gateway, and one of those is
    what made replication look healthy while the demo path was dead. The
    negative half re-reads Gitea's log for a denial newer than the attempt,
    because delivery can fail after the allowlist passes.
    """
    git_ip = _git_container_ip()
    before = _run(
        ["docker", "logs", "--tail", "400", SYNC_CONTAINER], check=False
    ).count("POST /hooks/wiki-update")

    # Gitea retries a failed task; asking it to redeliver is the cheapest real
    # attempt that does not require a new commit.
    _run(
        [
            "docker",
            "exec",
            GIT_CONTAINER,
            "sh",
            "-c",
            "curl -s -o /dev/null -w '%{http_code}' -X POST "
            "http://localhost:3000/api/v1/repos/snp-admin/snp-memory/hooks/1/tests "
            f"-H 'Authorization: token {_token()}'",
        ],
        check=False,
    )

    deadline = time.time() + 60
    after = before
    while time.time() < deadline:
        after = _run(
            ["docker", "logs", "--tail", "400", SYNC_CONTAINER], check=False
        ).count("POST /hooks/wiki-update")
        if after > before:
            break
        time.sleep(2)

    require(
        after > before,
        "no webhook delivery reached host-sync within 60s; Gitea is still not "
        "sending, or the hook is not firing",
    )

    recent = _run(["docker", "logs", "--tail", "60", SYNC_CONTAINER], check=False)
    require(
        git_ip in recent,
        f"a delivery arrived but not from the git container ({git_ip}). Only a "
        "delivery Gitea itself sent proves the demo path; a manual trigger from "
        "the host proves nothing",
    )

    gitea_recent = _run(["docker", "logs", "--tail", "40", GIT_CONTAINER], check=False)
    require(
        "webhook can only call allowed HTTP servers" not in gitea_recent,
        "Gitea is still refusing delivery with an allowlist denial",
    )
    return "WEBHOOK DELIVERY VERIFIED"


def _token() -> str:
    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("GITEA_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise GateFailure("GITEA_TOKEN not found in .env")


def group_push_to_replica() -> str:
    """A real push advances the replica with no manual trigger.

    This is W-2's mechanism end to end and the only check that exercises every
    hop at once: commit, push, Gitea's post-receive, webhook delivery, fetch,
    snapshot, symlink swap. It is measured against the commit id, so a replica
    that was already correct cannot pass by standing still.
    """
    head_before = _gitea_head()
    published_before = str(_sync_state().get("published_commit") or "")
    require(
        published_before == head_before,
        f"the replica ({published_before[:7]}) is not level with Gitea main "
        f"({head_before[:7]}) before the test; the result would be ambiguous",
    )

    pushed = _push_empty_probe_commit()
    deadline = time.time() + 120
    published_after = published_before
    while time.time() < deadline:
        state = _sync_state()
        published_after = str(state.get("published_commit") or "")
        if published_after == pushed:
            require(
                state.get("last_error") is None,
                f"the replica advanced but reports last_error="
                f"{state.get('last_error')!r}",
            )
            return "PUSH TO REPLICA VERIFIED"
        time.sleep(3)
    raise GateFailure(
        f"120s after pushing {pushed[:7]} the replica is still publishing "
        f"{published_after[:7]}. Nothing triggered it by hand, which is "
        "exactly the condition W-2 requires and does not survive"
    )


def _push_empty_probe_commit() -> str:
    """Push one empty commit to the vault repo's main and return its id.

    Empty so the corpus is untouched: this proves the transport, not a content
    change, and a demo rehearsal should not have to explain a stray edit.
    """
    work = REPO_ROOT / ".git" / "webhook-probe-clone"
    subprocess.run(["rm", "-rf", str(work)], check=False)
    _run(["git", "clone", "-q", "--depth", "1", GITEA_REMOTE, str(work)], timeout=300)
    subprocess.run(
        [
            "git",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "chore(sync): probe that a push reaches the replica unaided\n\n"
            "Empty by construction. Written by artifacts/v3/checks/"
            "webhook_delivery.py\nto verify Gitea's webhook now reaches host-sync "
            "after the\nALLOWED_HOST_LIST fix, which is the transport acceptance "
            "workflow W-2\ndepends on. Touches no page.",
        ],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    helper = f"!f() {{ echo username=snp-admin; echo password={_token()}; }}; f"
    subprocess.run(
        [
            "git",
            "-c",
            f"credential.helper={helper}",
            "push",
            "-q",
            "origin",
            "HEAD:main",
        ],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["rm", "-rf", str(work)], check=False)
    return head


def _porcelain() -> list[tuple[str, str]]:
    out = _run(["git", "status", "--porcelain", "--untracked-files=all"])
    entries: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        entries.append((line[:2].strip(), line[3:].strip().strip('"')))
    return entries


def group_tree_clean() -> str:
    """Everything is committed except a named set, and that set is justified.

    The classification is the assertion, not the list. An exclusion that turns
    out to be source, a test, a ledger or configuration fails here rather than
    being quietly carried as "known".
    """
    remaining = {path for _status, path in _porcelain()}
    unexpected = sorted(remaining - EXPECTED_EXCLUSIONS)
    require(
        not unexpected,
        f"{len(unexpected)} path(s) are uncommitted and unaccounted for: "
        f"{unexpected[:10]}",
    )

    disallowed_suffixes = (".py", ".md", ".yml", ".yaml", ".toml", ".json", ".sql")
    for path in sorted(EXPECTED_EXCLUSIONS & remaining):
        if path == "Untitled.md":
            continue  # classified below
        if path.startswith(".obsidian/") or path.startswith("docs/image"):
            continue  # editor state and unreferenced binaries are allowed out
        require(
            not path.endswith(disallowed_suffixes),
            f"excluded path {path} looks like source, config or documentation; "
            "only editor state, scratch and unreferenced binaries may be left out",
        )
    require(
        "Untitled.md" not in remaining
        or not (REPO_ROOT / "Untitled.md")
        .read_text(encoding="utf-8")
        .strip()
        .startswith("#"),
        "Untitled.md looks like authored documentation rather than scratch; it "
        "should not be silently excluded",
    )
    return "TREE CLEAN VERIFIED"


def _branch_paths() -> set[str]:
    """Every path touched by commits on this branch but not on its base."""
    base = _run(["git", "merge-base", FEATURE_BRANCH, "main"]).strip()
    out = _run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "diff",
            "--name-only",
            base,
            FEATURE_BRANCH,
        ]
    )
    return {line for line in out.splitlines() if line}


def group_history_hygiene() -> str:
    """No secret or editor-state file entered the branch's history.

    With a planted control, because an all-clear over an empty set is the same
    output as an all-clear over a real one, and only one of those means
    anything.
    """
    paths = _branch_paths()
    require(
        len(paths) > 50,
        f"only {len(paths)} paths differ from the base branch; this is not the "
        "week's work and a clean result would be meaningless",
    )

    offenders = sorted(
        p for p in paths if any(bad in p for bad in FORBIDDEN_IN_HISTORY)
    )
    require(
        not offenders,
        f"forbidden paths entered the branch history: {offenders[:10]}",
    )

    planted = set(paths) | {".secrets/scout_static_tokens.json"}
    control = sorted(
        p for p in planted if any(bad in p for bad in FORBIDDEN_IN_HISTORY)
    )
    require(
        control == [".secrets/scout_static_tokens.json"],
        f"the detector did not flag a planted secret path; it reported {control}. "
        "A scan that cannot find one proves nothing when it finds none",
    )
    return "HISTORY HYGIENE VERIFIED"


def group_committed_tree_green() -> str:
    """The committed tree passes the suite, ruff and mypy.

    Run only once the tree is clean of tracked modifications, so the suite is
    measuring what was committed rather than uncommitted edits that happen to
    be present.
    """
    # Deliberate exclusions are allowed to stay modified -- C1 already proved
    # every one of them is editor state, scratch or an unreferenced binary, and
    # none is read by the suite, ruff or mypy. Anything else being dirty would
    # mean this gate measures uncommitted work and calls it the commit.
    dirty = [
        path
        for status, path in _porcelain()
        if status and status != "??" and path not in EXPECTED_EXCLUSIONS
    ]
    require(
        not dirty,
        f"tracked files outside the excluded set are still modified, so this "
        f"would test uncommitted work rather than the commit: {dirty[:8]}",
    )

    _run([".venv/bin/ruff", "check", "."], timeout=300)
    _run([".venv/bin/ruff", "format", "--check", "."], timeout=300)
    _run([".venv/bin/mypy", "scout", "scripts"], timeout=900)

    suite = subprocess.run(
        ["uv", "run", "pytest", "-m", "not integration", "--disable-socket", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
        env=_suite_env(),
    )
    require(
        suite.returncode == 0,
        f"the offline suite failed against the committed tree:\n"
        f"{suite.stdout.strip()[-600:]}",
    )
    return "COMMITTED TREE GREEN"


def _suite_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.pop("LITELLM_BASE_URL", None)
    env.pop("LITELLM_MASTER_KEY", None)
    return env


def group_no_push() -> str:
    """The feature branch never left this machine."""
    remotes = _run(["git", "remote"]).split()
    for remote in remotes:
        listing = _run(
            ["git", "ls-remote", "--heads", remote, FEATURE_BRANCH], check=False
        )
        require(
            FEATURE_BRANCH not in listing,
            f"{FEATURE_BRANCH} exists on remote {remote!r}; R-6.4 requires the "
            "branch reach a remote only through a human-reviewed pull request",
        )

    local = _run(["git", "rev-parse", FEATURE_BRANCH]).strip()
    require(bool(local), "the feature branch does not exist locally")
    return "NO PUSH VERIFIED"


GROUPS: dict[str, Callable[[], str]] = {
    "setting-live": group_setting_live,
    "delivery": group_delivery,
    "push-to-replica": group_push_to_replica,
    "tree-clean": group_tree_clean,
    "history-hygiene": group_history_hygiene,
    "committed-tree-green": group_committed_tree_green,
    "no-push": group_no_push,
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
