"""Every input a workflow or an image build executes must be pinned.

A one-time pin decays the moment somebody adds a workflow or a Dockerfile, so
the pins are asserted here rather than recorded in a document. Measured
2026-08-26, before this file existed: 9 floating action references, 4 untagged
third-party images, 3 undigested base images, and one `curl … | sh` fetched
weekly on the runner that holds the push credential.

These tests read **parsed** YAML, never raw file text. The first version of this
check grepped the file and flagged the comment that documents the removed
`curl … | sh` line — a comment is not an executed step, and a check that cannot
tell the difference teaches people to delete the comment.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".gitea" / "workflows"

#: A pin is a commit. `astral-sh/setup-uv@v5` resolves to `e58605a9…` as an
#: annotated *tag object* and `d4b2f3b6…` as the commit; only the second names
#: the code that will run. Both are 40 hex characters, so this pattern cannot
#: tell them apart — `artifacts/superpowers/supply-chain-pins-2026-08-26.md`
#: records which was used and how it was resolved.
COMMIT_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")

PIPE_TO_SHELL = re.compile(r"(curl|wget)\b[^\n]*\|\s*(ba)?sh\b")


def _workflows() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.yaml"))


def _steps(workflow: Path) -> list[tuple[str, dict[str, Any]]]:
    document = yaml.safe_load(workflow.read_text())
    return [
        (job_name, step)
        for job_name, job in (document.get("jobs") or {}).items()
        for step in job.get("steps", [])
    ]


def test_there_are_workflows_to_check() -> None:
    """Guard against the whole suite passing because the glob found nothing."""
    assert _workflows(), f"no workflows under {WORKFLOW_DIR}"


@pytest.mark.parametrize("workflow", _workflows(), ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit(workflow: Path) -> None:
    floating = [
        f"{job}:{step.get('name')}: {step['uses']}"
        for job, step in _steps(workflow)
        if "uses" in step and not COMMIT_SHA.match(step["uses"])
    ]
    assert not floating, (
        "actions must be pinned to a 40-hex commit SHA, not a tag:\n  "
        + "\n  ".join(floating)
    )


@pytest.mark.parametrize("workflow", _workflows(), ids=lambda p: p.name)
def test_no_step_pipes_a_download_into_a_shell(workflow: Path) -> None:
    """The one exposure this hardening pass existed to remove.

    Unattended, on a self-hosted runner holding `BOT_TOKEN` and mounting the
    Docker socket, whatever the URL served that week ran as the runner user.
    """
    piped = [
        f"{job}:{step.get('name')}"
        for job, step in _steps(workflow)
        if PIPE_TO_SHELL.search(step.get("run") or "")
    ]
    assert not piped, (
        "install a pinned action instead of piping to a shell:\n  " + "\n  ".join(piped)
    )


COMPOSE = REPO_ROOT / "docker-compose.yml"

#: Images built from this repository, addressed by build context rather than
#: pulled. Their inputs are pinned by the Dockerfile tests below instead.
LOCALLY_BUILT = frozenset(
    {
        "${SNP_SCOUT_IMAGE:-snp-scout}",
        "${SNP_BASIC_MEMORY_IMAGE:-snp-basic-memory}",
        "${SNP_HOST_SYNC_IMAGE:-snp-host-sync}",
    }
)


def _compose_services() -> dict[str, dict[str, Any]]:
    """Read the Compose file directly, **not** `docker compose config`.

    `gitea-runner` sits behind `profiles: [runner]`, so the resolved config
    omits it unless the profile is passed — and it is the one service that
    mounts the Docker socket and registers against Gitea. A pin check that
    shells out to `docker compose config` would silently skip exactly the
    service the hardening pass is about.
    """
    return yaml.safe_load(COMPOSE.read_text()).get("services") or {}


def test_every_pulled_image_is_pinned_to_a_digest() -> None:
    floating = [
        f"{name}: {spec['image']}"
        for name, spec in _compose_services().items()
        if (image := spec.get("image"))
        and image not in LOCALLY_BUILT
        and "@sha256:" not in image
    ]
    assert not floating, (
        "third-party images must carry an immutable digest:\n  " + "\n  ".join(floating)
    )


def test_the_profile_gated_runner_is_covered() -> None:
    """Regression guard for the check that would have skipped it.

    Also documents what step 1 found: this service is declared and has never
    been started here, so the socket exposure is opt-in behind
    `docker compose --profile runner up`.
    """
    runner = _compose_services().get("gitea-runner")
    assert runner is not None, "gitea-runner disappeared from docker-compose.yml"
    assert "runner" in (runner.get("profiles") or [])
    assert "@sha256:" in runner["image"]


DOCKERFILES = (
    REPO_ROOT / "scout" / "Dockerfile",
    REPO_ROOT / "basic-memory" / "Dockerfile",
    REPO_ROOT / "scripts" / "Dockerfile.sync",
)

#: `RUN` bodies continue across trailing backslashes; a line-at-a-time check
#: reads `pip install --no-cache-dir \` as an unhashed install.
_CONTINUED = re.compile(r"\\\s*\n\s*")


def _run_commands(dockerfile: Path) -> list[str]:
    joined = _CONTINUED.sub(" ", dockerfile.read_text())
    return [
        line.partition(" ")[2]
        for line in joined.splitlines()
        if line.startswith("RUN ")
    ]


@pytest.mark.parametrize(
    "dockerfile", DOCKERFILES, ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_every_base_image_is_pinned_to_a_digest(dockerfile: Path) -> None:
    froms = [
        line for line in dockerfile.read_text().splitlines() if line.startswith("FROM ")
    ]
    assert froms, f"no FROM in {dockerfile}"
    unpinned = [line for line in froms if "@sha256:" not in line]
    assert not unpinned, f"{dockerfile} builds on a mutable tag:\n  " + "\n  ".join(
        unpinned
    )


@pytest.mark.parametrize(
    "dockerfile", DOCKERFILES, ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_every_local_image_carries_the_candidate_revision_label(
    dockerfile: Path,
) -> None:
    content = dockerfile.read_text(encoding="utf-8")

    assert "ARG SNP_GIT_REVISION=unknown" in content
    assert "LABEL org.opencontainers.image.revision=$SNP_GIT_REVISION" in content


@pytest.mark.parametrize(
    "dockerfile", DOCKERFILES, ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_every_pip_install_requires_hashes(dockerfile: Path) -> None:
    """Revision 2 of the plan generated a lock and installed from the old file.

    The lock existed, was correct, and was never read — so this asserts the
    wiring, not the artefact. `--require-hashes` also fails closed on an
    unpinned entry, which is why it is the check rather than "a lock exists".
    """
    loose = [
        command
        for command in _run_commands(dockerfile)
        if command.startswith("pip install") and "--require-hashes" not in command
    ]
    assert not loose, (
        f"{dockerfile} installs without hash verification:\n  " + "\n  ".join(loose)
    )


@pytest.mark.parametrize(
    "dockerfile", DOCKERFILES, ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_every_apt_install_names_a_version(dockerfile: Path) -> None:
    """Pins the packages. It does **not** pin the repository state — see the
    comment in `scripts/Dockerfile.sync`, which records why snapshot.debian.org
    was deferred rather than adopted untested."""
    for command in _run_commands(dockerfile):
        # Shell words, not a regex over the line. The first version of this
        # check pattern-matched and split `git=1:2.47.3-0+deb13u1` at the epoch
        # colon, then reported the version half as an unversioned package.
        for segment in command.split("&&"):
            if "apt-get install" not in segment:
                continue
            words = shlex.split(segment.split("apt-get install", 1)[1])
            unversioned = [w for w in words if not w.startswith("-") and "=" not in w]
            assert not unversioned, (
                f"{dockerfile}: apt packages without a version: {unversioned}"
            )


def test_the_locks_cover_every_hashed_install() -> None:
    """Every lock a Dockerfile installs from must exist and be fully hashed."""
    locks = {
        REPO_ROOT / "scout" / "requirements.lock",
        REPO_ROOT / "basic-memory" / "requirements.lock",
        REPO_ROOT / "scripts" / "sync-service.lock",
    }
    for lock in locks:
        assert lock.exists(), f"missing lock: {lock}"
        pinned = re.findall(r"^([A-Za-z0-9._-]+)==", lock.read_text(), re.M)
        assert pinned, f"{lock} pins nothing"
        # every pinned distribution must carry at least one hash
        blocks = re.split(r"\n(?=[A-Za-z0-9._-]+==)", lock.read_text())
        unhashed = [
            b.splitlines()[0]
            for b in blocks
            if re.match(r"^[A-Za-z0-9._-]+==", b) and "--hash=sha256:" not in b
        ]
        assert not unhashed, f"{lock}: entries without hashes {unhashed}"
