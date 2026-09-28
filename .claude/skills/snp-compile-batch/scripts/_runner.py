#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Run one `snpmemory` command in its checkout and return a receipt.

Shared by this skill's scripts. Deliberately dependency-free: the skill must
work from a clean environment with no install step, so everything here is
standard library and the project's own environment is resolved by `uv` on
demand rather than assumed to exist.

The receipt is the same shape the rest of the system uses, so a caller never
has to parse prose to learn what happened:

    {"schemaVersion": 1, "ok": bool, "command": ..., "input": {...},
     "checks": [{"name", "ok", "details"}], "checkCount": int}
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any


def find_checkout(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """The system checkout this skill's commands run in.

    Found by walking up rather than configured, because an agent's working
    directory is not something the skill controls. That works for the mirrors
    tracked in the repository (`.agent/`, `.claude/`), which always sit inside a
    checkout. It does not work for a copy an installer placed in another
    project, which has no checkout above it: there `SNP_REPO_ROOT` names one,
    and the skill's `SKILL.md` says so, because the failure receipt is the only
    other place the variable appears.
    """
    override = os.environ.get("SNP_REPO_ROOT")
    if override:
        candidate = pathlib.Path(override)
        return candidate if (candidate / "pyproject.toml").is_file() else None
    here = (start or pathlib.Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "scout").is_dir():
            return parent
    cwd = pathlib.Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").is_file() and (parent / "scout").is_dir():
            return parent
    return None


def receipt(
    command: str, ok: bool, inputs: dict[str, Any], checks: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "ok": ok,
        "command": command,
        "input": inputs,
        "checks": checks,
        "checkCount": len(checks),
    }


def _first_json_object(stream: str) -> Any:
    """The first JSON object in `stream`, or None. Tolerates leading noise."""
    start = stream.find("{")
    while start != -1:
        try:
            return json.loads(stream[start:])
        except json.JSONDecodeError:
            start = stream.find("{", start + 1)
    return None


def run(command: str, argv: list[str], inputs: dict[str, Any]) -> int:
    """Invoke `snpmemory <command> …` and report what happened, as a receipt."""
    checks: list[dict[str, Any]] = []

    root = find_checkout()
    checks.append(
        {
            "name": "checkout-found",
            "ok": root is not None,
            "details": str(root)
            if root
            else "no pyproject.toml with a scout/ package above this script or "
            "the working directory; set SNP_REPO_ROOT",
        }
    )
    if root is None:
        print(json.dumps(receipt(command, False, inputs, checks), indent=2))
        return 2

    uv = shutil.which("uv")
    checks.append(
        {
            "name": "uv-available",
            "ok": uv is not None,
            "details": uv
            or "uv is not on PATH; it resolves the project "
            "environment without an install step",
        }
    )
    if uv is None:
        print(json.dumps(receipt(command, False, inputs, checks), indent=2))
        return 2

    completed = subprocess.run(
        [uv, "run", "--project", str(root), "snpmemory", command, *argv],
        capture_output=True,
        text=True,
        cwd=root,
    )
    checks.append(
        {
            "name": "command-ran",
            "ok": True,
            "details": f"snpmemory {command} {' '.join(argv)} -> exit "
            f"{completed.returncode}",
        }
    )
    checks.append(
        {
            "name": "command-succeeded",
            "ok": completed.returncode == 0,
            "details": (completed.stdout or completed.stderr or "").strip()[:800]
            or f"exit {completed.returncode} with no output",
        }
    )
    ok = completed.returncode == 0

    # Whether the handle named a batch at all, reported as its own check so a
    # caller never has to infer it. `compile-status` used to exit 0 for a
    # handle that named nothing (measured 2026-09-21); since 2026-09-24 it
    # refuses with exit 3 and an `input_validation` envelope. Both shapes are
    # read here, so this receipt stays honest against either version of the
    # command.
    # Scan for the first JSON object rather than requiring the stream to begin
    # with one: `uv` prefixes a VIRTUAL_ENV warning when a script that is itself
    # running under `uv run --script` shells into a project, and a strict
    # `startswith("{")` silently skipped the envelope it was written to read.
    parsed: Any = _first_json_object(completed.stdout) or _first_json_object(
        completed.stderr
    )
    if isinstance(parsed, dict):
        detail = str(parsed.get("detail", ""))
        message = str(parsed.get("message", ""))
        absent = detail.startswith("no plan at") or message.startswith("no plan at")
        unreadable = detail.startswith("plan could not be read") or message.startswith(
            "plan could not be read"
        )
        if absent or unreadable or "detail" in parsed:
            checks.append(
                {
                    "name": "batch-exists",
                    "ok": not (absent or unreadable),
                    "details": (detail or message)[:300],
                }
            )
            ok = ok and not (absent or unreadable)

    body = receipt(command, ok, inputs, checks)
    if completed.stdout.strip():
        body["output"] = completed.stdout.strip()[:4000]
    print(json.dumps(body, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through the wrappers
    print(
        json.dumps(
            receipt(
                "runner",
                find_checkout() is not None,
                {},
                [
                    {
                        "name": "checkout-found",
                        "ok": find_checkout() is not None,
                        "details": str(find_checkout()),
                    }
                ],
            ),
            indent=2,
        )
    )
    sys.exit(0 if find_checkout() is not None else 2)
