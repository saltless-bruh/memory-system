"""How live-integration tests are gated, and why the two cases differ.

`tests/conftest.py` makes one distinction that is easy to get backwards:

* **Not selected** (`SNP_INTEGRATION_PROJECT` unset) → **skip**. Nobody asked
  for these. Failing here makes `uv run pytest` never report green on a laptop
  with no stack, which trains everyone to read past the summary line — and that
  is the condition under which a real regression gets waved through.
* **Selected but incomplete** (the variable is set, the rest is not) → **fail**,
  naming what is missing. Somebody asked for the live suite and it cannot run; a
  skip there hides the thing they were trying to test.

These tests run the gate as a subprocess rather than importing it, because the
fixture is autouse and reads the ambient environment — the only honest way to
check it is to run pytest with that environment set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: One real integration test, used as the probe. Any would do.
PROBE = "tests/test_ingest_v2.py::test_ingest_document_to_postgres"


def _run(**env_overrides: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            PROBE,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=env,
    )


def test_unselected_live_tests_skip_rather_than_error() -> None:
    """The default `pytest` run must be able to report green."""
    result = _run(SNP_INTEGRATION_PROJECT=None)

    assert "1 skipped" in result.stdout, result.stdout[-2000:]
    assert "error" not in result.stdout.lower().replace("errors=0", "")
    assert result.returncode == 0


def test_the_skip_message_says_how_to_run_them() -> None:
    """A skip that does not say how to unskip is a dead end."""
    result = _run(SNP_INTEGRATION_PROJECT=None)
    combined = result.stdout + result.stderr
    # `-q` hides skip reasons; ask for them explicitly.
    verbose = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            PROBE,
            "-rs",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env={**{k: v for k, v in os.environ.items() if k != "SNP_INTEGRATION_PROJECT"}},
    )
    combined += verbose.stdout
    assert "SNP_INTEGRATION_PROJECT=snp-memory-it" in combined, combined[-2000:]


def test_selected_but_unconfigured_live_tests_still_fail_loudly() -> None:
    """Asking for the live suite without a stack must not quietly skip."""
    result = _run(
        SNP_INTEGRATION_PROJECT="snp-memory-it",
        POSTGRES_HOST=None,
        POSTGRES_DB=None,
        POSTGRES_INGEST_USER=None,
        POSTGRES_INGEST_PASSWORD=None,
        POSTGRES_INGEST_PASSWORD_FILE=None,
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "live integration prerequisites are missing" in combined, combined[-2000:]
    assert "1 skipped" not in result.stdout


def test_a_non_integration_test_is_untouched_by_either_path() -> None:
    """The gate must not reach tests that never asked for a stack."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_types.py",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0
    assert "skipped" not in result.stdout
