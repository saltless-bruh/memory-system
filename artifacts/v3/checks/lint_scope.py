#!/usr/bin/env python3
"""Oracle for lint scope validation in the ruff configuration.

Verifies that:
1. Frozen audit artifacts are excluded from lint while live source remains checked
2. Live source violations were fixed rather than suppressed

Usage:
    .venv/bin/python artifacts/v3/checks/lint_scope.py --group <name>
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def _get_ruff_exclude() -> set[str]:
    """Extract exclude patterns from pyproject.toml ruff config."""
    pyproject = REPO_ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")

    # Find the [tool.ruff] section and extract exclude
    lines = content.splitlines()
    in_ruff_section = False
    exclude_lines = []

    for i, line in enumerate(lines):
        if line.strip() == "[tool.ruff]":
            in_ruff_section = True
        elif in_ruff_section and line.startswith("["):
            # We've moved to another section
            break
        elif in_ruff_section and line.strip().startswith("exclude"):
            # Found the exclude line(s)
            remainder = line.split("=", 1)[1]
            exclude_lines.append(remainder)
            # Check if it's a list that continues
            j = i + 1
            while j < len(lines) and not lines[j].startswith("["):
                stripped = lines[j].strip()
                if not stripped or stripped.startswith("#"):
                    j += 1
                    continue
                if stripped.startswith("]"):
                    exclude_lines.append(stripped)
                    break
                exclude_lines.append(stripped)
                j += 1

    exclude_text = " ".join(exclude_lines)

    # Parse the list using a simple regex
    # Handles ["path1", "path2"]
    pattern = re.compile(r'"([^"]+)"')
    matches = pattern.findall(exclude_text)
    return set(matches)


def group_exclusion_scope() -> str:
    """Verify exclusion covers only frozen audit evidence.

    Assertions:
    1. Every excluded path is under artifacts/audits/
    2. Nothing under scout/, scripts/, tests/, artifacts/v3/ is excluded
    3. A planted error under scout/ is still caught by ruff check
    """
    excluded = _get_ruff_exclude()

    # First assertion: all excluded paths must be under artifacts/audits/
    for path in excluded:
        require(
            path.startswith("artifacts/audits/"),
            f"exclusion {path!r} is outside artifacts/audits/ "
            "(frozen audit paths only)",
        )

    # Second assertion: verify forbidden areas have no exclusions
    forbidden_prefixes = ("scout/", "scripts/", "tests/", "artifacts/v3/")
    for path in excluded:
        for forbidden in forbidden_prefixes:
            require(
                not path.startswith(forbidden),
                f"exclusion {path!r} covers live source under {forbidden!r}",
            )

    # Third assertion: plant a deliberate lint violation in scout/ and verify
    # it is still caught
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        dir=REPO_ROOT / "scout",
        delete=False,
        prefix="test_lint_control_",
    ) as tmp:
        # Write code with a known lint violation (UP035: use collections.abc)
        tmp.write("from typing import Iterator\n")
        tmp.flush()
        tmp_path = Path(tmp.name)

    try:
        # Run ruff check on this specific file
        proc = subprocess.run(
            [".venv/bin/ruff", "check", str(tmp_path)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        # The exclusion should NOT hide this file, so ruff should detect the error
        require(
            proc.returncode != 0,
            "planted lint violation under scout/ was not caught by ruff check; "
            "the exclusion is hiding live source",
        )
    finally:
        # Clean up the temporary file
        tmp_path.unlink(missing_ok=True)

    return "LINT EXCLUSION SCOPE VERIFIED"


def group_no_new_suppressions() -> str:
    """Verify live source fixes added no new suppressions.

    Asserts that scripts/host_sync.py and tests/test_host_sync.py have no new
    # noqa or # type: ignore comments compared to git HEAD.
    """
    files_to_check = [
        "scripts/host_sync.py",
        "tests/test_host_sync.py",
    ]

    for file_path in files_to_check:
        full_path = REPO_ROOT / file_path

        # Get the current version
        current = full_path.read_text(encoding="utf-8")
        current_suppressions = set(
            re.finditer(r"#\s*(?:noqa|type:\s*ignore)", current, re.IGNORECASE)
        )
        current_suppression_count = len(current_suppressions)

        # Get the HEAD version
        proc = subprocess.run(
            ["git", "show", f"HEAD:{file_path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode != 0:
            # File doesn't exist in HEAD, so all suppressions are new
            require(
                current_suppression_count == 0,
                f"{file_path} is new and contains suppressions; "
                "fixes must not add suppressions",
            )
        else:
            head_version = proc.stdout
            head_suppressions = set(
                re.finditer(r"#\s*(?:noqa|type:\s*ignore)", head_version, re.IGNORECASE)
            )
            head_suppression_count = len(head_suppressions)

            require(
                current_suppression_count <= head_suppression_count,
                f"{file_path} added new suppressions; the fix should address "
                "the underlying violation, not suppress it",
            )

    return "NO NEW SUPPRESSIONS VERIFIED"


def group_no_retired_service_tests() -> str:
    """Verify no test exercises a retired server.

    Assertions:
    1. No test file actively tests basic_memory/snp-wiki containers
    2. Tests verifying the service is NOT present are allowed
    3. Plant a test file that attempts to start the retired container
    """
    # Patterns that indicate a test is actively exercising the retired service
    # rather than verifying it's been removed
    retired_test_patterns = [
        re.compile(r"docker.*exec.*basic-memory", re.IGNORECASE),
        re.compile(r'CONTAINER\s*=\s*["\']snp-memory-basic-memory', re.IGNORECASE),
        re.compile(r"basic_memory\.mcp\.\w+", re.IGNORECASE),
    ]

    # Get all test files
    test_dir = REPO_ROOT / "tests"
    test_files = list(test_dir.rglob("*.py"))

    # Scan test files for active use of retired services
    for test_file in test_files:
        content = test_file.read_text(encoding="utf-8")
        for pattern in retired_test_patterns:
            if pattern.search(content):
                relative = test_file.relative_to(REPO_ROOT)
                require(
                    False,
                    f"{relative} actively exercises retired basic-memory service; "
                    "the service was removed from the architecture",
                )

    # Plant a positive control: create a test file exercising the retired service
    # and verify it would be caught
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        dir=REPO_ROOT / "tests",
        delete=False,
        prefix="control_retired_",
    ) as tmp:
        tmp.write(
            "# Positive control: exercises the retired basic-memory\n"
            'CONTAINER = "snp-memory-basic-memory-1"\n'
            "def test_retired(): pass\n"
        )
        tmp.flush()
        tmp_path = Path(tmp.name)

    try:
        # Read the file back and check if our detector would catch it
        tmp_content = tmp_path.read_text(encoding="utf-8")
        caught = False
        for pattern in retired_test_patterns:
            if pattern.search(tmp_content):
                caught = True
                break

        require(
            caught,
            "retired service detector failed its positive control",
        )
    finally:
        # Clean up the temporary file
        tmp_path.unlink(missing_ok=True)

    return "NO RETIRED SERVICE TESTS VERIFIED"


GROUPS: dict[str, Callable[[], str]] = {
    "exclusion-scope": group_exclusion_scope,
    "no-new-suppressions": group_no_new_suppressions,
    "no-retired-service-tests": group_no_retired_service_tests,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
