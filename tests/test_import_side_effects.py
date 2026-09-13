"""Importing a module must not reconfigure the process.

`scout/cli/config.py` documents the hazard this guards: importing
`scripts/verify_addresses.py` used to call `dotenv.load_dotenv()` at module
scope, putting roughly thirty variables into `os.environ` — credentials among
them — as a side effect of an `import`, inherited by every subprocess.

Measured 2026-09-14: that leak reached the test suite. `mint` imports
`verify_addresses` lazily, so seven `tests/test_cli_authoring.py` tests left
`GIT_SYNC_USERNAME` set without `GIT_SYNC_PASSWORD_FILE`, and eleven
`tests/test_host_sync.py` tests then failed on a half-configured credential
pair — while passing in isolation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Modules that read `.env` for their own command-line use.
_ENV_READING_MODULES = ("scripts.verify_addresses", "scripts.verify_groundedness")


@pytest.mark.parametrize("module", _ENV_READING_MODULES)
def test_importing_a_script_does_not_mutate_the_environment(module: str) -> None:
    # A subprocess is the only honest check: an in-process import is cached, and
    # whatever leaked would already have leaked.
    probe = (
        "import os, sys;"
        f"sys.path.insert(0, {str(REPO_ROOT)!r});"
        "before = dict(os.environ);"
        f"__import__({module!r});"
        "added = sorted(set(os.environ) - set(before));"
        "print(','.join(added))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-800:]
    added = [name for name in result.stdout.strip().split(",") if name]
    assert not added, (
        f"importing {module} added {added} to os.environ; load configuration "
        "inside main() so an import stays inert"
    )
