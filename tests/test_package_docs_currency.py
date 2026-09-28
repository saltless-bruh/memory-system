"""The texts an agent or operator acts on must describe the system that exists.

`test_docs_surface_currency.py` bans retired tool *names*. The drift found in
the 2026-09-26 audit was never a name. It was a deleted server still described
as a second connection, a skill whose own flags exit 3, the third retrieval
tool called "deferred" fifty lines after it is documented, a CI entry point
that was deleted three weeks earlier, and a scope promise the wiki tier does
not keep. Every one of those survived a byte-identity mirror test, because a
mirror test proves the copies agree, not that any of them is true.

So these checks tie prose to the thing it describes: the CLI declarations, the
files on disk, the ingest constant, the lock file. When the code moves, the
test fails and names the sentence to change.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "packages" / "snp-agent"

#: Repository documents a reader is told to act on, as opposed to dated
#: records (`docs/AUDIT_*`, `docs/HANDOFF_*`, registers) that describe a past
#: state on purpose.
ACTIVE_DOCS = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/CONNECT_AGENTS.md",
    "docs/runbook.md",
    "docs/ARCHITECTURE_STATUS.md",
)


def _package_texts() -> list[str]:
    """Every Markdown file the portable package ships to an agent."""
    return sorted(
        str(path.relative_to(REPO_ROOT))
        for sub in ("instructions", "rules", "workflows", "skills")
        for path in (PACKAGE_DIR / sub).rglob("*.md")
    )


ACTIVE_TEXTS = (*ACTIVE_DOCS, *_package_texts())


def _paragraphs(relative: str) -> list[tuple[int, str]]:
    """Blank-line-separated paragraphs with the line each starts on.

    Paragraphs rather than lines, because the honest sentence and its marker
    are routinely split across a wrap: "The local server that did / both was
    deleted on 2026-09-24" names the server on one line and its deletion on
    the next.
    """
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    paragraphs: list[tuple[int, str]] = []
    start, buffer = 1, []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            if not buffer:
                start = number
            buffer.append(line)
        elif buffer:
            paragraphs.append((start, "\n".join(buffer)))
            buffer = []
    if buffer:
        paragraphs.append((start, "\n".join(buffer)))
    return paragraphs


# ── the deleted local stdio server ──────────────────────────────────────────

#: Phrases that describe a local `snpmemory` MCP server as something to
#: configure or connect to. It was deleted on 2026-09-24 (016ed43) and the one
#: remaining HTTP server took its name, so every such sentence now points the
#: name at the wrong component.
_LOCAL_SERVER = re.compile(
    r"local[ `]*(stdio[ `]*)?`?snpmemory`?[ `]*(stdio[ `]*)?server"
    r"|stdio[ `]*`?snpmemory`?"
    r"|local server"
    r"|\(local, stdio\)",
    re.IGNORECASE,
)


@pytest.mark.parametrize("relative", ACTIVE_TEXTS)
def test_no_active_text_describes_the_deleted_stdio_server(relative: str) -> None:
    offenders = [
        f"{relative}:{start}: {para.splitlines()[0].strip()[:80]}"
        for start, para in _paragraphs(relative)
        if _LOCAL_SERVER.search(para) and "deleted" not in para.lower()
    ]
    assert not offenders, (
        "the local snpmemory stdio server was deleted on 2026-09-24; these "
        "paragraphs still describe it as current:\n" + "\n".join(offenders)
    )


# ── documented CLI flags exist ──────────────────────────────────────────────


def _declared_flags(command: str) -> set[str]:
    from scout.cli import app  # noqa: F401  (registers the commands)
    from scout.cli.registry import GLOBAL_ARGS, REGISTRY

    spec = REGISTRY.get(command)
    assert spec is not None, f"documented command `snpmemory {command}` is undeclared"
    flags = {arg.name for arg in (*spec.args, *GLOBAL_ARGS)}
    flags |= {arg.short for arg in (*spec.args, *GLOBAL_ARGS) if arg.short}
    return flags | {"--format"}  # the documented alias of --output


def _skill_commands(text: str) -> set[str]:
    return set(re.findall(r"(?m)^\s*snpmemory\s+([a-z][a-z-]*)", text))


#: Skills whose code blocks run exactly one `snpmemory` command. For those,
#: every flag the file mentions can only mean a flag of that command.
SINGLE_COMMAND_SKILLS = [
    path
    for path in _package_texts()
    if path.endswith("SKILL.md")
    and len(_skill_commands((REPO_ROOT / path).read_text(encoding="utf-8"))) == 1
]


@pytest.mark.parametrize("relative", SINGLE_COMMAND_SKILLS)
def test_a_single_command_skill_names_only_flags_that_command_accepts(
    relative: str,
) -> None:
    """A skill about one command must not teach a flag the command rejects.

    snp-export-mcp told the agent to add `--all` or `--print` to
    `snpmemory mcp-config`. Those belong to `scripts/export_mcp_config.py`;
    the CLI rejects both with exit 3, so the skill failed on its own
    instructions. The flags were in prose, not in the code block, which is
    why every backticked flag in the file is checked, not only the commands.
    """
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    (command,) = _skill_commands(text)
    accepted = _declared_flags(command)
    named = set(re.findall(r"`[^`]*?(?<![\w-])(--[a-z][\w-]*)", text))
    named |= set(re.findall(r"(?m)^\s*snpmemory\s+\S+.*?(--[a-z][\w-]*)", text))
    unknown = sorted(named - accepted)
    assert not unknown, (
        f"{relative} documents `snpmemory {command}` with {unknown}, which it "
        f"does not accept; it accepts {sorted(accepted)}"
    )
