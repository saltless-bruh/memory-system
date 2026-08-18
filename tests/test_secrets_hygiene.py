"""tests/test_secrets_hygiene.py — Automated secret hygiene and leak prevention tests."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Real secret prefixes / specific revoked keys that must NEVER be present in tracked files.
SECRET_PATTERNS = [
    re.compile(p)
    for p in [
        r"sk-proj-[A-Za-z0-9_-]{20,}",
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"ghp_[A-Za-z0-9]{20,}",
        r"glpat-[A-Za-z0-9_-]{20,}",
        r"xoxb-[A-Za-z0-9_-]{20,}",
        r"sk-" + r"MQ3gEPcDNIdlP9TbKKLt9w",
    ]
]

ALLOWED_PLACEHOLDERS = [
    "sk-local-dev-placeholder",
    "sk-local-dev-change-me",
]


def get_tracked_files() -> list[Path]:
    """Get list of tracked files via git ls-files."""
    res = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        p = REPO_ROOT / line
        if p.is_file():
            files.append(p)
    return files


def test_zero_real_secrets_in_tracked_files() -> None:
    """Verify that zero real API keys or prohibited tokens exist in tracked files."""
    tracked = get_tracked_files()
    assert len(tracked) > 0, "Expected tracked files in repository"

    violations: list[str] = []
    current_test_file = Path(__file__).resolve()

    for path in tracked:
        if path.resolve() == current_test_file:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for pattern in SECRET_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                rel_path = path.relative_to(REPO_ROOT)
                violations.append(
                    f"{rel_path}: Found prohibited secret pattern matching {pattern.pattern} ({len(matches)} occurrences)"
                )

    msg = "Secret hygiene check failed:\n" + "\n".join(violations)
    assert not violations, msg


def test_safe_placeholders_used() -> None:
    """Verify that safe placeholders (sk-local-dev-placeholder / sk-local-dev-change-me) are used in config files."""
    env_example = REPO_ROOT / ".env.example"
    compose_file = REPO_ROOT / "docker-compose.yml"

    assert env_example.is_file(), ".env.example must exist"
    assert compose_file.is_file(), "docker-compose.yml must exist"

    env_content = env_example.read_text(encoding="utf-8")
    assert any(ph in env_content for ph in ALLOWED_PLACEHOLDERS), (
        ".env.example must use safe placeholder values"
    )

    compose_content = compose_file.read_text(encoding="utf-8")
    assert any(ph in compose_content for ph in ALLOWED_PLACEHOLDERS), (
        "docker-compose.yml must reference safe default placeholders"
    )
