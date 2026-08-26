"""An agent must never be told to call a tool that does not exist.

An agent's tool namespace comes from the **key** its client config gives a
server. Every surface that configures a client — `scripts/export_mcp_config.py`,
`scripts/install-agent.sh`, and the package manifest — names the wiki server
`snp-wiki`. For a long time the distributed instructions told agents to call
`basic-memory.search_notes(...)` instead, which is a tool no agent configured by
this repository has ever had.

The distinction this file encodes, because it is the whole subtlety of the fix:

* `basic-memory` is **correct** where it names the *engine* (the wiki software)
  or the *container* (the compose service). Those are real things with that
  name.
* `basic-memory` is **wrong** where it names something an agent connects to or
  calls. There it must be `snp-wiki`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The three trees whose contents are read by an agent as instructions.
CONTRACT_ROOTS = (
    REPO_ROOT / ".agent",
    REPO_ROOT / ".claude",
    REPO_ROOT / "packages" / "snp-agent",
)

#: `<server>.<tool>` — the shape of a call an agent would actually attempt.
_TOOL_CALL = re.compile(
    r"basic-memory\.\s*(search_notes|read_note|write_note|list_notes)"
)

#: The only places `basic-memory` may still appear, and why. Anything else is a
#: new occurrence that has to be classified deliberately rather than absorbed.
ALLOWED = {
    # Names the wiki engine, alongside the script it runs with.
    "instructions/Roadmap.instructions.md": "the LLM-Wiki engine",
    # Lists the docker compose services the bootstrap skill brings up.
    "skills/snp-bootstrap-system/SKILL.md": "the compose service",
}


def _contract_files() -> list[Path]:
    found = [
        path
        for root in CONTRACT_ROOTS
        if root.is_dir()
        for path in sorted(root.rglob("*.md"))
    ]
    assert found, "no agent contract files found — the roots moved"
    return found


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.mark.parametrize("path", _contract_files(), ids=_rel)
def test_no_instruction_names_a_tool_that_does_not_exist(path: Path) -> None:
    """`basic-memory.search_notes` is not a tool any configured agent has."""
    hits = _TOOL_CALL.findall(path.read_text(encoding="utf-8"))
    assert not hits, (
        f"{_rel(path)}: instructs the agent to call basic-memory.{hits[0]}; the "
        "client-config key is `snp-wiki`, so that tool does not exist"
    )


def test_every_surviving_engine_mention_is_a_deliberate_one() -> None:
    """New `basic-memory` prose must be classified, not absorbed silently."""
    unexplained: list[str] = []
    for path in _contract_files():
        if "basic-memory" not in path.read_text(encoding="utf-8"):
            continue
        suffix = next(
            (key for key in ALLOWED if _rel(path).endswith(key)),
            None,
        )
        if suffix is None:
            unexplained.append(_rel(path))

    assert not unexplained, (
        "these files mention `basic-memory` and are not on the allowlist — "
        "decide whether each names the engine/container (keep) or a server an "
        f"agent talks to (rename to snp-wiki): {unexplained}"
    )


def test_the_wiki_server_is_named_consistently_across_every_surface() -> None:
    """The config emitters and the instructions must agree on one key."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    import scripts.export_mcp_config as exporter

    emitted = set(exporter.generate_config("claude")["mcpServers"])
    assert "snp-wiki" in emitted
    assert "basic-memory" not in emitted

    installer = (REPO_ROOT / "scripts" / "install-agent.sh").read_text(encoding="utf-8")
    assert '"snp-wiki"' in installer
    assert '"basic-memory"' not in installer
