"""`snp-wiki`'s half of the requiredTools claim, checked against the container.

The offline suite covers `snpmemory` and `scout` because both build in-process.
`snp-wiki` is basic-memory running in a container, so verifying it needs Docker
and cannot live in a hermetic suite. Splitting it out is what lets plugin.json's
note name a real test for every server instead of implying one test covers all
three -- which is how `list_notes` stayed in the manifest while never being
served.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONTAINER = "snp-memory-basic-memory-1"

_PROBE = (
    "import asyncio, importlib, json;"
    "importlib.import_module('basic_memory.mcp.tools');"
    "from basic_memory.mcp.server import mcp;"
    "print(json.dumps(sorted(t.name for t in asyncio.run(mcp.list_tools()))))"
)


def _served() -> set[str]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    probe = subprocess.run(
        ["docker", "exec", CONTAINER, "python", "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if probe.returncode != 0:
        pytest.skip(f"{CONTAINER} is not running: {probe.stderr.strip()[:120]}")
    return set(json.loads(probe.stdout))


def test_the_manifest_names_only_wiki_tools_the_container_serves() -> None:
    served = _served()
    declared = json.loads(
        (REPO_ROOT / "packages" / "snp-agent" / "plugin.json").read_text(encoding="utf-8")
    )["extensions"]["io.snp.memory"]["requiredTools"]["snp-wiki"]

    assert served, "the probe returned no tools; it cannot prove anything"
    missing = sorted(set(declared) - served)
    assert not missing, f"plugin.json names snp-wiki tools that are not served: {missing}"


def test_the_probe_can_detect_an_unserved_tool() -> None:
    """The control for the test above.

    A membership check against an empty or mis-parsed set passes trivially, so
    prove the same comparison rejects a name the server does not serve.
    """
    served = _served()
    assert "list_notes" not in served, (
        "this control assumed list_notes is unserved; basic-memory now serves it "
        "and the control needs a different name"
    )
    assert sorted({"list_notes"} - served) == ["list_notes"]
