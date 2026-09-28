"""The documented first-run path must work from a fresh clone.

Every step here was followed by hand at some point and worked, on a machine
that already had what the step forgot: a virtual environment, an untracked
Compose overlay, a `.env` written before the example drifted. A fresh clone
has none of that, so these checks read the repository the way a new operator
does.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
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


# ── scripts/bootstrap.sh ────────────────────────────────────────────────────

#: What uv prints when asked to `pip install` with no environment to install
#: into. The fake below reproduces that refusal, because it is the behaviour
#: the README path depended on never meeting: uv does not fall back to the
#: system interpreter.
_UV_NO_VENV = (
    "error: No virtual environment found; run `uv venv` to create an "
    "environment, or pass `--system` to install into a non-virtual environment"
)

_FAKE_UV = """#!/usr/bin/env bash
echo "$*" >> "$UV_LOG"
case "$1" in
  sync|venv)
    mkdir -p .venv/bin
    ln -sf "$REAL_PYTHON" .venv/bin/python
    echo 'export VIRTUAL_ENV="$PWD/.venv"' > .venv/bin/activate
    ;;
  pip)
    if [ -z "${VIRTUAL_ENV:-}" ] && [ ! -x .venv/bin/python ]; then
      echo '%s' >&2
      exit 2
    fi
    ;;
esac
"""


def _fresh_clone(tmp_path: Path) -> Path:
    """The files bootstrap.sh touches, as a new clone has them.

    The two Python helpers are replaced by stubs that record they ran: their
    own behaviour has its own tests, and the real ones would reach for the
    real repository.
    """
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "bootstrap.sh", clone / "scripts")
    shutil.copy2(REPO_ROOT / ".env.example", clone / ".env.example")
    for helper in ("bootstrap_secrets.py", "gen_index.py"):
        (clone / "scripts" / helper).write_text(
            "import sys\n"
            f"open('ran.log', 'a').write('{helper} ' + ' '.join(sys.argv[1:]) + '\\n')\n",
            encoding="utf-8",
        )
    return clone


def test_bootstrap_leaves_the_venv_the_readme_activates(tmp_path: Path) -> None:
    """README: `./scripts/bootstrap.sh`, then `source .venv/bin/activate`.

    bootstrap.sh ran `uv pip install -e . -e .[dev]` without ever creating an
    environment, so under `set -e` uv's refusal stopped it at the install
    step and there was no `.venv` to activate.
    """
    clone = _fresh_clone(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(_FAKE_UV % _UV_NO_VENV, encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
    }
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        UV_LOG=str(tmp_path / "uv.log"),
        REAL_PYTHON=sys.executable,
    )

    result = subprocess.run(
        ["bash", "scripts/bootstrap.sh"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (clone / ".venv" / "bin" / "activate").is_file()
    assert (clone / ".env").is_file()
    ran = (clone / "ran.log").read_text(encoding="utf-8")
    assert "gen_index.py --check" in ran
    labels = re.findall(r"\[(\d+)/(\d+)\]", result.stdout)
    assert labels, result.stdout
    assert {total for _, total in labels} == {str(len(labels))}, labels
    assert [int(step) for step, _ in labels] == list(range(1, len(labels) + 1))


# ── .env.example ────────────────────────────────────────────────────────────

#: The Compose files a default deployment interpolates from `.env`. The
#: staging, integration and DNS overlays take their own prefixed variables
#: and document them where they are used.
DEPLOYMENT_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.private-git.yml")

#: Where a variable in `.env` can be consumed: Compose interpolation, the
#: LiteLLM config, and the host-side CLI and scripts.
CONSUMER_ROOTS = ("docker-compose.yml", "docker-compose.private-git.yml", "config")
CONSUMER_PACKAGES = ("scout", "scripts")

_EXAMPLE = REPO_ROOT / ".env.example"


def _example_names() -> tuple[set[str], dict[str, str]]:
    """Every variable the example mentions, and those it actually assigns."""
    text = _EXAMPLE.read_text(encoding="utf-8")
    mentioned = set(re.findall(r"(?m)^#?\s*([A-Z][A-Za-z0-9_]*)=", text))
    assigned = dict(re.findall(r"(?m)^([A-Z][A-Za-z0-9_]*)=(.*)$", text))
    return mentioned, assigned


def _interpolated(relative: str) -> set[str]:
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", text))


def test_env_example_names_every_variable_the_deployment_reads() -> None:
    """A fresh `.env` is a copy of the example. A variable Compose reads that
    the example never mentions is one an operator cannot know to set: the
    judge route's key and model, the Gitea runner, the image names, the
    Scout port and the private-Git identity were all missing."""
    mentioned, _ = _example_names()
    needed = set().union(*(_interpolated(f) for f in DEPLOYMENT_COMPOSE_FILES))
    missing = sorted(needed - mentioned)
    assert not missing, f".env.example never mentions {missing}"


#: Settings of services V3 removed. `scout/cli/config.py` still forwards the
#: names to REMOTE commands, so the generic check below cannot see that
#: nothing consumes them; they are named here instead.
RETIRED_SETTINGS = ("BASIC_MEMORY_PORT", "BASIC_MEMORY_URL")


def test_env_example_carries_no_retired_service_setting() -> None:
    """`BASIC_MEMORY_PORT=8765` survived the removal of basic-memory by a
    full version: a setting that configures nothing reads as a service that
    exists."""
    mentioned, _ = _example_names()
    stale = sorted(set(RETIRED_SETTINGS) & mentioned)
    assert not stale, f".env.example still configures removed services: {stale}"


def test_env_example_assigns_nothing_no_component_reads() -> None:
    _, assigned = _example_names()
    corpus = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for root in CONSUMER_ROOTS
        for path in (
            [REPO_ROOT / root]
            if (REPO_ROOT / root).is_file()
            else sorted((REPO_ROOT / root).rglob("*.yaml"))
        )
    )
    corpus += "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for package in CONSUMER_PACKAGES
        for path in sorted((REPO_ROOT / package).rglob("*.py"))
    )
    unread = sorted(name for name in assigned if name not in corpus)
    assert not unread, f".env.example assigns variables nothing reads: {unread}"


def test_env_example_passes_no_host_path_into_a_container() -> None:
    """Compose interpolates `.env` into container environments. A host-relative
    path there names a file the container does not have: `RAW_ACL_FILE=
    ./raw/.acl.yaml` would replace sync-job's `/data/raw/.acl.yaml` default,
    and an ingester that cannot read its ACL policy publishes nothing."""
    _, assigned = _example_names()
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    offenders = []
    for service, spec in compose["services"].items():
        environment = spec.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(item.split("=", 1) for item in environment)
        for key, value in environment.items():
            for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", str(value)):
                example = assigned.get(name, "").strip().strip("'\"")
                if example.startswith(("./", "../", "~")):
                    offenders.append(f"{service}.{key} <- {name}={example}")
    assert not offenders, "host paths reach container environments:\n" + (
        "\n".join(offenders)
    )


# ── documented image builds stamp their revision ───────────────────────────

#: Texts that tell an operator or an agent to build the deployment's images.
BUILD_DOCS = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/runbook.md",
    "packages/snp-agent/skills/snp-bootstrap-system/SKILL.md",
    "packages/snp-agent/instructions/agent_guide.instructions.md",
)


def _compose_commands(text: str) -> list[str]:
    """Each `docker compose` command, fenced or inline, continuations joined."""
    joined = text.replace("\\\n", " ")
    fenced = re.findall(r"```[a-z]*\n(.*?)```", joined, re.DOTALL)
    commands = [
        line.strip()
        for block in fenced
        for line in block.splitlines()
        if "docker compose" in line
    ]
    commands += re.findall(r"`([^`\n]*docker compose[^`\n]*)`", joined)
    return commands


def test_every_documented_deployment_build_stamps_its_revision() -> None:
    """A build without `SNP_GIT_REVISION` labels its images `unknown`, which
    the preflight reports as unverifiable and `snpmemory status` as degraded.
    README's quick start built exactly that way. The disposable integration
    project is exempt: its images are never the deployment's. A procedure
    that exports the variable once, earlier in its own section, is stamped."""
    offenders = []
    for relative in BUILD_DOCS:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for section in re.split(r"(?m)^## ", text):
            exported = "export SNP_GIT_REVISION=" in section
            for command in _compose_commands(section):
                builds = "--build" in command or re.search(
                    r"\bcompose\b.*\bbuild\b", command
                )
                if not builds or "docker-compose.integration.yml" in command:
                    continue
                if not exported and "SNP_GIT_REVISION" not in command:
                    offenders.append(f"{relative}: {command}")
    assert not offenders, "unstamped builds:\n" + "\n".join(offenders)
