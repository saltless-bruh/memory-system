"""Executable boundaries between active operating docs and preserved history."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS = REPO_ROOT / "docs" / "ARCHITECTURE_STATUS.md"
VERIFIER = REPO_ROOT / "scripts" / "verify_addresses.py"

#: Every tree that ships the agent contract. `.agent/` is authoritative.
AGENT_TREES = (".agent", ".claude", "packages/snp-agent")

ACTIVE_FILES = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "docs" / "runbook.md",
    REPO_ROOT / "docs" / "basic-memory-setup.md",
    REPO_ROOT / "docs" / "CONNECT_AGENTS.md",
    REPO_ROOT / "docs" / "DEMO.md",
)

HISTORICAL_FILES = (
    REPO_ROOT / "docs" / "SESSION_HANDOVER_AND_V2_ROADMAP.md",
    REPO_ROOT / "docs" / "sprint" / "IMPLEMENTATION_PLAN.md",
    REPO_ROOT / "docs" / "rag_failure_analysis.md",
    REPO_ROOT / "docs" / "HIGH_THROUGHPUT_INFERENCE_BLUEPRINT.md",
)

PROHIBITED_ACTIVE_PATTERNS = {
    "retired local model runtime": re.compile(r"\bollama\b", re.IGNORECASE),
    "retired monolithic RAG runtime": re.compile(r"rag-anything", re.IGNORECASE),
    "developer checkout read-write mount": re.compile(r"(?:^|\s)/repo(?::rw)?\b"),
    "legacy caller scope": re.compile(
        r"Scope\s*\(\s*(?:roles|team)|Scope\.(?:roles|team)", re.IGNORECASE
    ),
    "source-controlled database secret": re.compile(
        r"rag_app_secret|postgres_master_secret", re.IGNORECASE
    ),
    "unsupported DOCX ingestion": re.compile(r"\bdocx\b", re.IGNORECASE),
    # M3: `.agent/workflows/snp-verify.md` told agents to require
    # "score $\ge 0.70$". No such threshold exists in `verify_addresses.py`,
    # and none could: `RagChunk.score` carries RRF weights capped near 0.033,
    # so any 0.0-1.0 floor is unreachable by construction.
    "similarity threshold absent from the verifier": re.compile(
        r"\b(?:score|similarity)\b[^\n]{0,20}?(?:\$?\\ge\$?|>=|≥|≧)\s*\$?\s*0?\.\d",
        re.IGNORECASE,
    ),
    # M4: AGENTS.md claimed a mismatched hint "returns empty, silently" and
    # that "retrieval dead-ends". `rag_fetch` passes `path=` to the backend, so
    # a bad hint returns the whole file — the hint governs ranking, not
    # existence.
    "mismatched hint claimed to retrieve nothing": re.compile(
        r"returns? empty[,\s]*\*{0,2}\s*silently|retrieval dead[- ]ends?",
        re.IGNORECASE,
    ),
}


def _active_guidance() -> list[Path]:
    files = list(ACTIVE_FILES)
    for root in (REPO_ROOT / ".agent", REPO_ROOT / "packages" / "snp-agent"):
        files.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(files)


def test_inventory_classifies_every_document_authority() -> None:
    content = STATUS.read_text(encoding="utf-8")
    for heading in (
        "## Active documents",
        "## Current implementation baseline",
        "## Historical and reference documents",
        "## Prohibited claims in active guidance",
    ):
        assert heading in content
    for path in ACTIVE_FILES:
        assert f"`{path.relative_to(REPO_ROOT).as_posix()}`" in content
    assert "`wiki/index.md` is generated output" in content
    assert "`raw/` is evidence" in content
    assert "`artifacts/superpowers/`" in content


def test_active_guidance_contains_no_retired_architecture_claims() -> None:
    violations: list[str] = []
    for path in _active_guidance():
        content = path.read_text(encoding="utf-8")
        for label, pattern in PROHIBITED_ACTIVE_PATTERNS.items():
            if pattern.search(content):
                violations.append(f"{path.relative_to(REPO_ROOT)}: {label}")
    assert violations == []


def test_every_historical_document_has_an_authority_banner() -> None:
    paths = list(HISTORICAL_FILES) + sorted(
        (REPO_ROOT / "docs" / "proposal").glob("*.md")
    )
    for path in paths:
        opening = "\n".join(path.read_text(encoding="utf-8").splitlines()[:5])
        assert re.search(
            r"HISTORICAL|SUPERSEDED|DOMAIN REFERENCE, NOT SNP DEPLOYMENT AUTHORITY",
            opening,
        ), path.relative_to(REPO_ROOT)


def test_portable_agent_files_are_exact_mirrors() -> None:
    package_root = REPO_ROOT / "packages" / "snp-agent"
    agent_root = REPO_ROOT / ".agent"
    package_files = sorted(path for path in package_root.rglob("*") if path.is_file())
    assert package_files
    for package_path in package_files:
        relative = package_path.relative_to(package_root)
        agent_path = agent_root / relative
        assert agent_path.is_file(), relative
        assert agent_path.read_bytes() == package_path.read_bytes(), relative


def test_address_criterion_in_docs_is_the_one_the_verifier_runs() -> None:
    """The workflow must name the constants that actually decide a PASS.

    `/snp-verify` used to instruct agents to require `score >= 0.70` — a
    threshold that never existed in code (finding M3). It now names `TOP_RANK`
    and `GROUNDING_MIN_COVERAGE`, and this test fails if either constant
    disappears from `scripts/verify_addresses.py` or stops being named in any
    tree's copy of the workflow, so the prose cannot drift away from the gate
    again without a red test.
    """
    source = VERIFIER.read_text(encoding="utf-8")
    for constant in ("TOP_RANK", "GROUNDING_MIN_COVERAGE"):
        assert re.search(rf"^{constant}\s*=", source, re.MULTILINE), (
            f"scripts/verify_addresses.py no longer defines {constant}"
        )
    for tree in AGENT_TREES:
        path = REPO_ROOT / tree / "workflows" / "snp-verify.md"
        content = path.read_text(encoding="utf-8")
        for constant in ("TOP_RANK", "GROUNDING_MIN_COVERAGE"):
            assert constant in content, (
                f"{path.relative_to(REPO_ROOT)} does not name {constant}"
            )


def test_live_test_docs_name_the_fail_closed_host_prerequisites() -> None:
    required = (
        "SNP_INTEGRATION_PROJECT",
        "POSTGRES_QUERY_PASSWORD_FILE",
        "POSTGRES_INGEST_PASSWORD_FILE",
        "POSTGRES_MIGRATION_PASSWORD_FILE",
        "LITELLM_BASE_URL",
        "LITELLM_MASTER_KEY",
        "SCOUT_INTEGRATION_URL",
        "SCOUT_INTEGRATION_INFRA_TOKEN_FILE",
    )
    for path in (REPO_ROOT / "README.md", REPO_ROOT / "docs" / "runbook.md"):
        content = path.read_text(encoding="utf-8")
        for name in required:
            assert name in content, f"{path.relative_to(REPO_ROOT)}: {name}"
