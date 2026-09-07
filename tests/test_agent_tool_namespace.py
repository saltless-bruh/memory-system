"""Agent-facing contracts may name only the active V3 retrieval surface."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOTS = (
    REPO_ROOT / ".agent",
    REPO_ROOT / ".claude",
    REPO_ROOT / "packages" / "snp-agent",
)
RETIRED_SURFACE = re.compile(
    r"\b(?:snp-wiki|basic-memory|rag_fetch|search_notes|read_note|write_note|list_notes)\b",
    re.IGNORECASE,
)


def _contract_files() -> list[Path]:
    files = [REPO_ROOT / "AGENTS.md", REPO_ROOT / "CLAUDE.md"]
    files.extend(
        path
        for root in CONTRACT_ROOTS
        if root.is_dir()
        for path in sorted(root.rglob("*.md"))
        if not any(part.startswith("superpowers") for part in path.parts)
    )
    assert files, "no agent contract files found — the roots moved"
    return files


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.mark.parametrize("path", _contract_files(), ids=_rel)
def test_contracts_name_no_retired_retrieval_surface(path: Path) -> None:
    hits = sorted(set(RETIRED_SURFACE.findall(path.read_text(encoding="utf-8"))))
    assert not hits, f"{_rel(path)} names retired agent surface(s): {hits}"


def test_config_emitters_advertise_exactly_the_two_v3_servers() -> None:
    import scripts.export_mcp_config as exporter

    emitted = set(exporter.generate_config("claude")["mcpServers"])
    assert emitted == {"scout", "snpmemory"}

    installer = (REPO_ROOT / "scripts" / "install-agent.sh").read_text(encoding="utf-8")
    assert '"scout"' in installer
    assert '"snpmemory"' in installer
    assert '"snp-wiki"' not in installer
    assert '"basic-memory"' not in installer


@pytest.mark.parametrize(
    "relative",
    (
        Path("instructions/query_protocol.instructions.md"),
        Path("workflows/snp-query.md"),
        Path("skills/snp-query-wiki/SKILL.md"),
    ),
)
def test_entry_contracts_put_search_before_read(relative: Path) -> None:
    content = (REPO_ROOT / "packages" / "snp-agent" / relative).read_text(
        encoding="utf-8"
    )
    assert 0 <= content.find("wiki_search") < content.find("wiki_read")
