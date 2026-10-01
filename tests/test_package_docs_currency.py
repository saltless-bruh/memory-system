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


#: A line that starts a unit of its own even without a blank line before it:
#: a list item, or a table row.
_BLOCK_START = re.compile(r"^(?:[-*] |\d+\. |\|)")


def _paragraphs(relative: str) -> list[tuple[int, str]]:
    """Paragraphs, list items and table rows, with the line each starts on.

    Units rather than lines, because the honest sentence and its marker are
    routinely split across a wrap: "The local server that did / both was
    deleted on 2026-09-24" names the server on one line and its deletion on
    the next. Units rather than blank-line paragraphs, because a bullet list
    is one blank-line paragraph, and a "deleted" in one bullet would excuse
    a stale claim three bullets further down.
    """
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    units: list[tuple[int, str]] = []
    start, buffer = 1, []
    for number, line in enumerate(text.splitlines(), start=1):
        if buffer and (not line.strip() or _BLOCK_START.match(line)):
            units.append((start, "\n".join(buffer)))
            buffer = []
        if line.strip():
            if not buffer:
                start = number
            buffer.append(line)
    if buffer:
        units.append((start, "\n".join(buffer)))
    return units


# ── the deleted local stdio server ──────────────────────────────────────────

#: Phrases that describe a local `snpmemory` MCP server as something to
#: configure or connect to. It was deleted on 2026-09-24 (a3dcd16) and the one
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


# ── wiki_quote is the third retrieval tool ──────────────────────────────────

#: The texts that walk an agent through retrieval. Each must name the tool
#: that returns a page's source passage, or the agent is left believing no
#: such tool exists.
RETRIEVAL_TEXTS = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs/CONNECT_AGENTS.md",
    "packages/snp-agent/rules/snp-memory.md",
    "packages/snp-agent/instructions/query_protocol.instructions.md",
    "packages/snp-agent/instructions/agent_guide.instructions.md",
    "packages/snp-agent/workflows/snp-query.md",
    "packages/snp-agent/skills/snp-query-wiki/SKILL.md",
)

#: Wording that tells an agent source extraction does not exist. It was true
#: until 2026-09-21, when `wiki_quote` shipped.
_EXTRACTION_DEFERRED = re.compile(
    r"source extraction (beyond (an )?indexed wiki pages? )?is (a )?deferred"
    r"|external source extraction is a deferred"
    r"|source-reading (tool|operation)",
    re.IGNORECASE,
)


@pytest.mark.parametrize("relative", RETRIEVAL_TEXTS)
def test_retrieval_texts_name_wiki_quote(relative: str) -> None:
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert "wiki_quote" in text, (
        f"{relative} walks an agent through retrieval without naming wiki_quote"
    )


@pytest.mark.parametrize("relative", ACTIVE_TEXTS)
def test_no_active_text_calls_source_extraction_deferred(relative: str) -> None:
    offenders = [
        f"{relative}:{start}: {match.group(0)}"
        for start, para in _paragraphs(relative)
        for match in _EXTRACTION_DEFERRED.finditer(para.replace("\n", " "))
    ]
    assert not offenders, (
        "wiki_quote has served source passages since 2026-09-21:\n"
        + "\n".join(offenders)
    )


# ── the removed healer and its CI gate ──────────────────────────────────────

_REFERENCED_FILE = re.compile(
    r"(?<![\w./-])((?:scripts|\.gitea/workflows)/[\w.-]+\.(?:py|ya?ml|sh))"
)


@pytest.mark.parametrize("relative", ACTIVE_TEXTS)
def test_every_script_or_workflow_an_active_text_names_exists(relative: str) -> None:
    """`scripts/ci_address_gate.py` and `auto-healer.yaml` were deleted on
    2026-09-06 (29f1f50) and README, the runbook's CI table and ARCHITECTURE_
    STATUS kept presenting them as the CI entry point. A paragraph may still
    name a removed file when it says so."""
    # A skill's `scripts/` is its own directory, not the repository's.
    bases = (REPO_ROOT, (REPO_ROOT / relative).parent)
    offenders = []
    for start, para in _paragraphs(relative):
        if re.search(r"\b(removed|deleted)\b", para, re.IGNORECASE):
            continue
        for match in _REFERENCED_FILE.finditer(para):
            if not any((base / match.group(1)).exists() for base in bases):
                offenders.append(f"{relative}:{start}: {match.group(1)}")
    assert not offenders, "active text names files that do not exist:\n" + (
        "\n".join(offenders)
    )


@pytest.mark.parametrize("relative", ACTIVE_TEXTS)
def test_no_active_text_presents_the_healer_as_current(relative: str) -> None:
    pattern = re.compile(
        r"auto-healer\.yaml|heal pass|healer pass|heal/\*|run a healer",
        re.IGNORECASE,
    )
    offenders = [
        f"{relative}:{start}: {para.splitlines()[0].strip()[:80]}"
        for start, para in _paragraphs(relative)
        if pattern.search(para)
        and not re.search(r"\b(removed|deleted)\b", para, re.IGNORECASE)
    ]
    assert not offenders, "the auto-heal subsystem was removed 2026-09-06:\n" + (
        "\n".join(offenders)
    )


# ── the agent package's source of truth ─────────────────────────────────────

_AGENT_DIR_AUTHORITATIVE = re.compile(
    r"`\.agent/` is (the )?authoritative"
    r"|edit `\.agent/`, then mirror"
    r"|live only in the package",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    "relative",
    (
        "README.md",
        "CLAUDE.md",
        "docs/ARCHITECTURE_STATUS.md",
        "tests/test_agent_package_sync.py",
    ),
)
def test_package_source_of_truth_is_named_consistently(relative: str) -> None:
    """`export_agent_bundle.py --sync` copies packages/snp-agent -> .agent/ ->
    .claude/. A text naming `.agent/` as the place to edit sends the editor
    to the tree the sync overwrites."""
    text = (REPO_ROOT / relative).read_text(encoding="utf-8")
    offenders = [m.group(0) for m in _AGENT_DIR_AUTHORITATIVE.finditer(text)]
    assert not offenders, f"{relative}: {offenders}"


def test_readme_names_the_package_as_the_edit_point() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "export_agent_bundle.py --sync" in text


# ── department scoping on the wiki tier ─────────────────────────────────────


def test_docs_do_not_promise_wiki_department_scoping_the_code_lacks() -> None:
    """Every wiki page is indexed for every department, and `wiki_read` never
    compares a page's department to the caller's. Until that changes the
    docs must say so, and say it is planned (owner ruling, 2026-09-26),
    rather than imply the filesystem route is the only thing that bypasses
    scoping. When wiki scoping lands this test fails, and the fix is to
    rewrite those sentences, not this assertion."""
    from scout.policy import CANONICAL_DEPARTMENTS
    from scout.wiki_ingest import WIKI_ALLOWED_DEPARTMENTS

    if set(WIKI_ALLOWED_DEPARTMENTS) != set(CANONICAL_DEPARTMENTS):
        pytest.skip("wiki pages carry their own ACL; re-derive this check")
    for relative in (
        "AGENTS.md",
        "README.md",
        "CLAUDE.md",
        "packages/snp-agent/rules/snp-memory.md",
    ):
        text = " ".join(
            (REPO_ROOT / relative).read_text(encoding="utf-8").split()
        ).lower()
        assert "not yet department-scoped" in text, (
            f"{relative} must state that wiki pages are not yet "
            "department-scoped while wiki_ingest grants every department"
        )
        assert "bypass scope enforcement" not in text, relative


# ── figure and table extraction ─────────────────────────────────────────────


def test_architecture_status_does_not_forbid_a_true_extraction_claim() -> None:
    lock = (REPO_ROOT / "scout" / "requirements.lock").read_text(encoding="utf-8")
    shipped = {
        name for name in ("pdfplumber", "pillow") if re.search(rf"(?m)^{name}==", lock)
    }
    if shipped != {"pdfplumber", "pillow"}:
        pytest.skip("the image no longer ships both extractors")
    text = " ".join(
        (REPO_ROOT / "docs" / "ARCHITECTURE_STATUS.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for stale in ("installs `pypdf` only", "are both absent"):
        assert stale not in text, (
            f"ARCHITECTURE_STATUS still says {stale!r} while "
            "scout/requirements.lock pins pdfplumber and pillow"
        )
