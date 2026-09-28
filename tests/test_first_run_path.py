"""The documented first-run path must work from a fresh clone.

Every step here was followed by hand at some point and worked, on a machine
that already had what the step forgot: a virtual environment, an untracked
Compose overlay, a `.env` written before the example drifted. A fresh clone
has none of that, so these checks read the repository the way a new operator
does.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Documents that tell an operator what to put in `COMPOSE_FILE`.
OPERATOR_DOCS = ("README.md", "docs/runbook.md", ".env.example")


def _compose_files_named_in(relative: str) -> set[str]:
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    named: set[str] = set()
    for value in re.findall(r"(?m)^#?\s*COMPOSE_FILE=['\"]?([^'\"\s]+)", text):
        named.update(part for part in value.split(":") if part)
    return named


def test_every_compose_file_the_docs_select_exists() -> None:
    """The runbook told operators to set
    `COMPOSE_FILE=docker-compose.yml:docker-compose.private-git.yml` while the
    overlay existed only in one working tree, so `docker compose config`
    failed on any fresh checkout and the private-repo sync path, the source of
    every vault publication, could not be reproduced from git."""
    named = set().union(*(_compose_files_named_in(doc) for doc in OPERATOR_DOCS))
    assert named, "no COMPOSE_FILE example found; re-derive this check"
    missing = sorted(name for name in named if not (REPO_ROOT / name).is_file())
    assert not missing, f"docs select Compose files the repo lacks: {missing}"


def test_private_git_overlay_carries_no_literal_secret() -> None:
    """The overlay is committed because it holds only references: the username
    is interpolated from `.env` and the token is a Compose secret read from
    an ignored file. A literal value in either place would make committing it
    a leak, so the shape is pinned rather than trusted."""
    overlay = yaml.safe_load(
        (REPO_ROOT / "docker-compose.private-git.yml").read_text(encoding="utf-8")
    )
    environment = overlay["services"]["host-sync"]["environment"]
    for key, value in environment.items():
        assert value.startswith(("${", "/run/secrets/")), (key, value)
    for name, secret in overlay["secrets"].items():
        assert set(secret) == {"file"}, name
        assert secret["file"].startswith("${"), name
