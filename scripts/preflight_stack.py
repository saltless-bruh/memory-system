#!/usr/bin/env python3
"""Preflight checks for the two deployment faults that hide behind other errors.

Both conditions this module checks took the stack down on 2026-08-24, and
neither announced itself. A container created while the host advertised only a
loopback resolver reports `500` from the gateway. An `snp-scout` image older
than the checkout reports `400 ... at most 100 requests can be in one batch` —
a provider error, for a cap the repository already respects.

Diagnosing either from its symptom costs an hour. Both are one string
comparison away from being obvious, so they are made obvious here.

The classification is pure text: `classify_resolv_conf` and
`classify_image_revision` never touch Docker, git, or the network, which is why
`tests/test_preflight_stack.py` can pin the exact files the broken stack
carried. Only `main()` shells out.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scripts.write_release_manifest import (
    DEFAULT_LIVE_PROJECT,
    ManifestError,
    compose_command,
    validate_compose_project,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Docker's embedded resolver. Present in every container on a user-defined
#: network, so its presence says nothing about reaching the outside world.
_EMBEDDED_RESOLVER = "127.0.0.11"

#: Docker writes this comment when the host file it copied named no resolver it
#: could forward to. It is the whole diagnosis, and it is already in the file.
_NO_EXTERNAL = "NO EXTERNAL NAMESERVERS DEFINED"

#: The upstreams Docker's embedded resolver will forward to, when there are any.
_EXTSERVERS_RE = re.compile(r"^#\s*ExtServers:\s*\[(?P<servers>.*)\]\s*$", re.MULTILINE)

_NAMESERVER_RE = re.compile(r"^\s*nameserver\s+(?P<address>\S+)", re.MULTILINE)


def _compose_text(
    project: str, *arguments: str, compose_files: tuple[str, ...] = ()
) -> str:
    """Render a human remedy while retaining a plain default-project command."""
    rendered = " ".join(arguments)
    files = "".join(f" --file {shlex.quote(filename)}" for filename in compose_files)
    if project == DEFAULT_LIVE_PROJECT:
        return f"docker compose{files} {rendered}"
    return f"docker compose --project-name {project}{files} {rendered}"


def _recreate_remedy(project: str, *, compose_files: tuple[str, ...] = ()) -> str:
    return _compose_text(
        project,
        "up",
        "-d",
        "--force-recreate",
        "litellm",
        "scout",
        "sync-job",
        compose_files=compose_files,
    )


def _scout_image_environment(image: str) -> str:
    """Set an explicit non-default Scout image tag for Compose remedies."""
    return "" if image == "snp-scout" else f"SNP_SCOUT_IMAGE={shlex.quote(image)} "


def _rebuild_remedy(
    project: str, image: str = "snp-scout", *, compose_files: tuple[str, ...] = ()
) -> str:
    build = _compose_text(project, "build", "scout", compose_files=compose_files)
    recreate = _compose_text(
        project,
        "up",
        "-d",
        "--force-recreate",
        "scout",
        "sync-job",
        compose_files=compose_files,
    )
    image_environment = _scout_image_environment(image)
    return (
        f"SNP_GIT_REVISION=$(git rev-parse HEAD) {image_environment}{build} && "
        f"{image_environment}{recreate}"
    )


@dataclass(frozen=True, slots=True)
class Finding:
    """One check's verdict, with the command that fixes it.

    Attributes:
        check: Stable identifier for the check, for machine readers.
        ok: True when the checked condition is satisfied.
        detail: What was actually observed — never a guess.
        remedy: The exact command to run. Empty when nothing is wrong.
        available: False when the check's input could not be read at all. A
            check that did not run is not a check that passed, and it must not
            be reported as one.
    """

    check: str
    ok: bool
    detail: str
    remedy: str = ""
    available: bool = True


def classify_resolv_conf(
    text: str,
    *,
    compose_project: str = DEFAULT_LIVE_PROJECT,
    compose_files: tuple[str, ...] = (),
) -> Finding:
    """Decide whether a container's `/etc/resolv.conf` can reach the outside.

    A `nameserver` line is not sufficient evidence: every container on a
    user-defined network lists Docker's own resolver at `127.0.0.11`, which
    resolves other services happily while having no upstream for anything else.
    """
    if _NO_EXTERNAL in text:
        return Finding(
            check="container-dns",
            ok=False,
            detail=(
                f"{_NO_EXTERNAL}: Docker copied the host resolver at container-creation "
                "time and it had no forwardable upstream"
            ),
            remedy=_recreate_remedy(compose_project, compose_files=compose_files),
        )

    match = _EXTSERVERS_RE.search(text)
    if match:
        servers = match.group("servers").strip()
        if servers:
            return Finding(
                check="container-dns",
                ok=True,
                detail=f"embedded resolver forwards to {servers}",
            )
        return Finding(
            check="container-dns",
            ok=False,
            detail="embedded resolver lists an empty ExtServers set",
            remedy=_recreate_remedy(compose_project, compose_files=compose_files),
        )

    external = [
        address
        for address in _NAMESERVER_RE.findall(text)
        if address != _EMBEDDED_RESOLVER and not address.startswith("127.")
    ]
    if external:
        return Finding(
            check="container-dns",
            ok=True,
            detail=f"resolv.conf names {', '.join(external)}",
        )

    return Finding(
        check="container-dns",
        ok=False,
        detail=(
            "resolv.conf names no resolver outside the loopback range; nothing here "
            "can reach an external host"
        ),
        remedy=_recreate_remedy(compose_project, compose_files=compose_files),
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 stamp, tolerating Docker's 9-digit fractional seconds."""
    if not value:
        return None
    trimmed = re.sub(r"\.(\d{6})\d+", r".\1", value.strip())
    try:
        return datetime.fromisoformat(trimmed)
    except ValueError:
        return None


def classify_image_revision(
    *,
    image_revision: str | None,
    head_revision: str,
    image_created: str | None,
    head_committed: str | None,
    compose_project: str = DEFAULT_LIVE_PROJECT,
    image: str = "snp-scout",
    compose_files: tuple[str, ...] = (),
) -> Finding:
    """Decide whether the running image was built from the current checkout.

    A timestamp comparison is a fallback, not an equivalent: an image built from
    a dirty tree, or from a different branch, can be newer than `HEAD` and still
    be running code the repository does not contain. So a newer *unlabelled*
    image is reported as unverifiable rather than as healthy — the honest answer
    when the only evidence available cannot decide the question.
    """
    if image_revision:
        if image_revision == head_revision:
            return Finding(
                check="image-revision",
                ok=True,
                detail=f"image built from {head_revision[:7]}",
            )
        return Finding(
            check="image-revision",
            ok=False,
            detail=(
                f"image was built from {image_revision[:7]}, checkout is at "
                f"{head_revision[:7]}"
            ),
            remedy=_rebuild_remedy(compose_project, image, compose_files=compose_files),
        )

    built = _parse_timestamp(image_created)
    committed = _parse_timestamp(head_committed)
    if built is not None and committed is not None and built < committed:
        return Finding(
            check="image-revision",
            ok=False,
            detail=(
                f"image carries no revision label and was built {built.isoformat()}, "
                f"before HEAD was committed {committed.isoformat()}"
            ),
            remedy=_rebuild_remedy(compose_project, image, compose_files=compose_files),
        )

    return Finding(
        check="image-revision",
        ok=False,
        detail=(
            "image carries no revision label, so what it was built from cannot be "
            "established; a build timestamp alone does not prove it matches the checkout"
        ),
        remedy=_rebuild_remedy(compose_project, image, compose_files=compose_files),
    )


def exit_code_for(findings: list[Finding]) -> int:
    """Map findings onto the project's exit-code contract.

    `0` every check passed · `1` a check ran and found a real problem ·
    `2` a check could not run at all, which is an infrastructure fault and must
    never be mistaken for a clean run. `2` outranks `1`: if the stack cannot be
    inspected, whatever else was observed is not the whole picture.
    """
    if not findings or any(not finding.available for finding in findings):
        return 2
    return 0 if all(finding.ok for finding in findings) else 1


def _run(command: list[str]) -> str | None:
    """Return a command's stdout, or None when it cannot be run."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_ROOT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def collect_findings(
    service: str = "litellm",
    image: str = "snp-scout",
    compose_project: str = DEFAULT_LIVE_PROJECT,
    compose_files: tuple[str, ...] = (),
) -> list[Finding]:
    """Run both checks against the live stack, skipping what cannot be read."""
    findings: list[Finding] = []
    try:
        project = validate_compose_project(compose_project, allow_live_project=True)
    except ManifestError as exc:
        return [
            Finding(
                check="compose-project",
                ok=False,
                detail=str(exc),
                available=False,
            )
        ]

    resolv = _run(
        compose_command(
            project,
            "exec",
            "-T",
            service,
            "cat",
            "/etc/resolv.conf",
            allow_live_project=True,
            compose_files=compose_files,
        )
    )
    if resolv is None:
        findings.append(
            Finding(
                check="container-dns",
                ok=False,
                detail=f"could not read /etc/resolv.conf from the {service!r} service",
                remedy=_compose_text(
                    project, "up", "-d", service, compose_files=compose_files
                ),
                available=False,
            )
        )
    else:
        findings.append(
            classify_resolv_conf(
                resolv, compose_project=project, compose_files=compose_files
            )
        )

    inspected = _run(
        [
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            '{{.Created}}|{{index .Config.Labels "org.opencontainers.image.revision"}}',
        ]
    )
    head_revision = _run(["git", "rev-parse", "HEAD"])
    head_committed = _run(["git", "log", "-1", "--format=%cI"])
    if inspected is None or not head_revision:
        missing = "the image" if inspected is None else "the git checkout"
        findings.append(
            Finding(
                check="image-revision",
                ok=False,
                detail=f"could not inspect {missing}",
                remedy=_rebuild_remedy(project, image, compose_files=compose_files),
                available=False,
            )
        )
    else:
        created, _, revision = inspected.partition("|")
        findings.append(
            classify_image_revision(
                image_revision=revision.strip() or None,
                head_revision=head_revision,
                image_created=created.strip() or None,
                head_committed=head_committed,
                compose_project=project,
                image=image,
                compose_files=compose_files,
            )
        )

    return findings


def main(argv: list[str] | None = None) -> int:
    """Report both checks and exit on the project's contract."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--service",
        default="litellm",
        help="compose service whose resolv.conf is read (default: litellm)",
    )
    parser.add_argument(
        "--image", default="snp-scout", help="image tag to check for drift"
    )
    parser.add_argument(
        "--compose-project",
        default=DEFAULT_LIVE_PROJECT,
        help="explicit Compose project to inspect (default: snp-memory)",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        default=[],
        metavar="PATH",
        help="Compose file to use; repeat in precedence order for overlays",
    )
    args = parser.parse_args(argv)

    findings = collect_findings(
        service=args.service,
        image=args.image,
        compose_project=args.compose_project,
        compose_files=tuple(args.compose_file),
    )
    if not findings:
        print(
            "preflight could not run: docker and git must both be available from "
            f"{REPO_ROOT}",
            file=sys.stderr,
        )
        return 2

    for finding in findings:
        status = "PASS" if finding.ok else "FAIL" if finding.available else "UNAVAIL"
        print(f"{status} {finding.check}: {finding.detail}")
        if not finding.ok and finding.remedy:
            print(f"     fix: {finding.remedy}")
    return exit_code_for(findings)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
