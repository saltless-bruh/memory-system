#!/usr/bin/env python3
"""Independent oracles for container stack validation (leaf-1.5.1).

Four groups validate that the rebuilt dev stack:
  - runs this branch's code with vault mounts
  - has no retired services
  - serves the V3 tool surface
  - preserves the ingested corpus

Each group prints a success token after every assertion passes, or exits
non-zero with a failure message if any assertion fails.

Usage:
    .venv/bin/python artifacts/v3/checks/containers.py --group <name>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_FILE = REPO_ROOT / "artifacts" / "v3" / "checks" / "baseline.json"


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


# --------------------------------------------------------------------------
# G1: Container image code verification
# --------------------------------------------------------------------------


def group_image() -> str:
    """Verify the running scout contains wiki_ingest.py and vault-replica mount.

    Asserts:
      - wiki_ingest.py exists in the running container
      - its content matches the working tree
      - vault-replica is mounted and is read-only
    """
    import docker  # type: ignore[import-untyped]  # noqa: PLC0415

    client = docker.from_env()
    try:
        container = client.containers.get("snp-memory-scout-1")
    except Exception as exc:
        raise GateFailure(f"could not find snp-memory-scout-1: {exc}") from exc

    def _check_file_match(container_path: str, working_tree_rel: str) -> None:
        """Compare a file in container against working tree using SHA-256."""
        try:
            _, output = container.exec_run(f"cat {container_path}")
            container_content = output.decode("utf-8")
        except Exception as exc:
            raise GateFailure(
                f"{working_tree_rel} not found in container: {exc}"
            ) from exc

        working_tree_path = REPO_ROOT / working_tree_rel
        require(
            working_tree_path.exists(),
            f"{working_tree_rel} not found at {working_tree_path}",
        )
        working_tree_content = working_tree_path.read_text(encoding="utf-8")

        # Hash both to compare exactly
        container_hash = hashlib.sha256(container_content.encode("utf-8")).hexdigest()
        working_tree_hash = hashlib.sha256(
            working_tree_content.encode("utf-8")
        ).hexdigest()

        require(
            container_hash == working_tree_hash,
            f"{working_tree_rel} content mismatch: container {container_hash} != "
            f"working tree {working_tree_hash}",
        )

    # Check wiki_ingest.py: holds the log.md exclusion and V3 tool surface
    _check_file_match("/app/scout/wiki_ingest.py", "scout/wiki_ingest.py")

    # Check pgvector.py: holds DEFAULT_DENSE_TIMEOUT_SECONDS = 3.0 fix.
    # Without this, non-English queries silently return nothing.
    _check_file_match("/app/scout/backends/pgvector.py", "scout/backends/pgvector.py")

    # Check for vault-replica mount and read-only status
    inspect_cmd = [
        "docker",
        "inspect",
        "snp-memory-scout-1",
        "--format={{json .Mounts}}",
    ]
    try:
        result = subprocess.run(inspect_cmd, capture_output=True, text=True, check=True)
        mounts = json.loads(result.stdout)
    except Exception as exc:
        raise GateFailure(f"could not inspect container mounts: {exc}") from exc

    vault_mount = None
    for mount in mounts:
        if isinstance(mount, dict) and mount.get("Destination") == "/vault-replica":
            vault_mount = mount
            break

    require(
        vault_mount is not None,
        "vault-replica mount not found on scout container",
    )
    assert vault_mount is not None  # For mypy narrowing
    require(
        vault_mount.get("RW") is False,
        "vault-replica mount is writable; must be read-only (INV-1)",
    )

    return "CONTAINER CODE VERIFIED"


# --------------------------------------------------------------------------
# G2: Retired service absence with planted control
# --------------------------------------------------------------------------


def group_no_retired_service() -> str:
    """Verify basic-memory service is NOT running, with positive control.

    First demonstrates the detector works by checking it finds a planted
    fixture containing basic-memory. Then asserts basic-memory is absent
    from the real snp-memory project stack.
    """
    import docker  # noqa: PLC0415, type: ignore[import-untyped]

    client = docker.from_env()

    # Positive control: detector must find a known planted fixture
    fixture_list = [
        {"Names": ["/snp-memory-other-1"], "State": "running"},
        {"Names": ["/snp-memory-basic-memory-1"], "State": "running"},
    ]

    def detector(service_list: list[dict[str, Any]]) -> bool:
        """Return True if basic-memory is found in the list."""
        for item in service_list:
            names = item.get("Names", [])
            if any("basic-memory" in name for name in names):
                return True
        return False

    control_detected = detector(fixture_list)
    require(
        control_detected,
        "detector failed its positive control; it cannot find planted basic-memory",
    )

    # Real assertion: basic-memory must not be in the actual running containers
    # Only check the snp-memory project, not snp-memory-it-* (integration) or others
    try:
        containers = client.containers.list()
        running_names = [
            name
            for c in containers
            for name in c.name.split(",")
            if name.startswith("snp-memory-") and not name.startswith("snp-memory-it-")
        ]
    except Exception as exc:
        raise GateFailure(f"could not list containers: {exc}") from exc

    basic_memory_found = any("basic-memory" in name for name in running_names)
    require(
        not basic_memory_found,
        "basic-memory service is still running in snp-memory project",
    )

    return "RETIRED SERVICE ABSENT VERIFIED"


# --------------------------------------------------------------------------
# G3: Live tool surface verification
# --------------------------------------------------------------------------


def group_live_tool_surface() -> str:
    """Verify the live scout container serves exactly wiki_search and wiki_read.

    Reaches the container's HTTP MCP server, authenticates with static token,
    and verifies the tool manifest by initializing an MCP session.
    """
    import asyncio  # noqa: PLC0415

    try:
        from fastmcp import Client  # noqa: PLC0415
        from fastmcp.client.transports import (  # noqa: PLC0415
            StreamableHttpTransport,
        )
    except ImportError as exc:
        raise GateFailure(
            f"fastmcp library not available; cannot test live surface: {exc}"
        ) from exc

    # Read static token from secrets
    tokens_file = REPO_ROOT / ".secrets" / "scout_static_tokens.json"
    require(
        tokens_file.exists(),
        f"static tokens file not found at {tokens_file}",
    )

    try:
        tokens_data = json.loads(tokens_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GateFailure(f"could not parse static tokens: {exc}") from exc

    # Extract a valid token from the tokens file
    token: str | None = None
    if isinstance(tokens_data, dict):
        # The file maps token -> metadata, get the first valid token
        for t, metadata in tokens_data.items():
            if isinstance(metadata, dict) and metadata.get("subject"):
                token = t
                break

    require(
        token is not None, "no valid static token found in scout_static_tokens.json"
    )
    assert isinstance(token, str)  # For mypy narrowing

    # Connect to the live MCP server and get tool list
    async def list_tools() -> set[str]:
        transport = StreamableHttpTransport(
            "http://127.0.0.1:8080/mcp",
            auth=token,
        )
        try:
            async with Client(transport) as client:
                tools = await client.list_tools()
                return {tool.name for tool in tools}
        except Exception as exc:
            raise GateFailure(f"could not list tools from scout: {exc}") from exc

    try:
        tool_names = asyncio.run(list_tools())
    except GateFailure:
        raise
    except Exception as exc:
        raise GateFailure(f"MCP client error: {exc}") from exc

    expected_tools = {"wiki_search", "wiki_read"}
    require(
        tool_names == expected_tools,
        f"tool surface mismatch: found {sorted(tool_names)!r}, "
        f"expected {sorted(expected_tools)!r}",
    )

    return "LIVE TOOL SURFACE VERIFIED"


# --------------------------------------------------------------------------
# G4: Corpus survival verification
# --------------------------------------------------------------------------


def group_corpus_survived() -> str:
    """Verify ingested corpus survived rebuild against baseline.

    Compares live document and chunk counts against the recorded baseline,
    and asserts zero chunks have null embeddings.
    """
    import docker  # noqa: PLC0415, type: ignore[import-untyped]

    # Load baseline
    require(
        BASELINE_FILE.exists(),
        f"baseline file not found at {BASELINE_FILE}; "
        "run oracle once to capture baseline before rebuilding",
    )

    try:
        baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GateFailure(f"could not parse baseline: {exc}") from exc

    baseline_docs = baseline.get("documents")
    baseline_chunks = baseline.get("chunks")

    require(
        baseline_docs is not None and baseline_chunks is not None,
        f"baseline incomplete: {baseline!r}",
    )

    # Query live database
    client = docker.from_env()
    try:
        postgres = client.containers.get("snp-memory-postgres-1")
    except Exception as exc:
        raise GateFailure(f"postgres container not found: {exc}") from exc

    def run_query(query: str) -> str:
        exit_code, output = postgres.exec_run(
            ["psql", "-U", "postgres", "-d", "snp_rag", "-t", "-c", query]
        )
        require(
            exit_code == 0,
            f"psql query failed: {output.decode('utf-8')}",
        )
        return output.decode("utf-8").strip()  # type: ignore[no-any-return]

    # Get current counts
    doc_count_str = run_query("SELECT COUNT(*) FROM rag_documents;")
    chunk_count_str = run_query("SELECT COUNT(*) FROM rag_chunks;")
    null_count_str = run_query(
        "SELECT COUNT(*) FROM rag_chunks WHERE embedding IS NULL;"
    )

    try:
        doc_count = int(doc_count_str)
        chunk_count = int(chunk_count_str)
        null_count = int(null_count_str)
    except ValueError as exc:
        raise GateFailure(
            f"could not parse counts: docs={doc_count_str!r}, "
            f"chunks={chunk_count_str!r}, nulls={null_count_str!r}"
        ) from exc

    # Compare against baseline
    require(
        doc_count == baseline_docs,
        f"document count mismatch: {doc_count} != baseline {baseline_docs}",
    )
    require(
        chunk_count == baseline_chunks,
        f"chunk count mismatch: {chunk_count} != baseline {baseline_chunks}",
    )
    require(
        null_count == 0,
        f"found {null_count} chunks with null embedding; expected 0",
    )

    return "CORPUS SURVIVED VERIFIED"


# --------------------------------------------------------------------------
# Group registry and main
# --------------------------------------------------------------------------


GROUPS: dict[str, Callable[[], str]] = {
    "image": group_image,
    "no-retired-service": group_no_retired_service,
    "live-tool-surface": group_live_tool_surface,
    "corpus-survived": group_corpus_survived,
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
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR [{args.group}] {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
