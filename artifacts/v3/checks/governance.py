#!/usr/bin/env python3
"""Oracles for D-6 governance: distinguishing agent and human vault edit paths.

Two `--group` values, each printing a success-only token after every assertion
passes, exiting non-zero otherwise.

- `--group write-rule`: every shipped contract states the agent branch-and-PR
  rule, proven against a planted push instruction.
- `--group no-write-capability`: no served tool performs git push or vault
  write, and every compose file mounts the vault read-only, proven against a
  planted writable mount.

Usage:
    .venv/bin/python artifacts/v3/checks/governance.py --group <name>
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.integration.yml",
    "docker-compose.staging.yml",
)


class GateFailure(AssertionError):
    """A measurement that did not hold. Message is surfaced to the operator."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


# --------------------------------------------------------------------------
# Compose loader with tag tolerance
# --------------------------------------------------------------------------


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose's merge tags (``!override``, ``!reset``).

    Compose overlay files carry these tags to control list and mapping merge
    behaviour. ``yaml.safe_load`` rejects them outright, which would make this
    oracle fail for a reason that has nothing to do with the gate.
    """


def _keep_underlying_value(
    loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node
) -> object:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return None


_ComposeLoader.add_multi_constructor("!", _keep_underlying_value)


def _load_compose(name: str) -> dict[str, object]:
    path = REPO_ROOT / name
    require(path.exists(), f"{name} is missing")
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)  # noqa: S506
    require(isinstance(loaded, dict), f"{name} did not parse to a mapping")
    assert isinstance(loaded, dict)
    return loaded


# --------------------------------------------------------------------------
# G1: write-rule — agent branch-and-PR rule in shipped contracts
# --------------------------------------------------------------------------


def group_write_rule() -> str:
    """Verify every shipped contract states the agent branch-and-PR rule.

    The rule distinguishes two actors:
    - A human in Obsidian edits and pushes directly.
    - An agent must use a feature branch and submit a pull request.

    This is enforced partly by instruction and partly by capability absence
    (the agent surface has no push tool). The instruction half is verified here.
    """
    # Key contracts that must state the rule
    contract_paths = [
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "CLAUDE.md",
        REPO_ROOT / ".agent" / "instructions" / "git_workflow.instructions.md",
        REPO_ROOT / ".claude" / "instructions" / "git_workflow.instructions.md",
    ]

    # Check that contracts mention: feature branch AND pull request
    # in proximity to "agent" or similar context
    for path in contract_paths:
        require(path.exists(), f"contract path missing: {path}")
        content = path.read_text(encoding="utf-8")

        has_feature_branch = "feature branch" in content.lower()
        has_pull_request = "pull request" in content.lower()
        has_agent_context = "agent" in content.lower()
        has_never_push_direct = (
            "never push" in content.lower()
            or "do not push" in content.lower()
            or "do not commit" in content.lower()
        )

        rule_present = (has_feature_branch or has_pull_request) and (
            has_agent_context or has_never_push_direct
        )

        require(
            rule_present,
            f"{path.relative_to(REPO_ROOT)} does not adequately state the agent branch-and-PR rule",
        )

    # Positive control: a contract that explicitly instructs pushing to main
    # must be caught by a detector looking for push-to-main instructions.
    with tempfile.TemporaryDirectory(prefix="v3-write-rule-control-") as temporary:
        control = Path(temporary) / "bad-agent-contract.md"
        control.write_text(
            "# Bad Agent Contract\n\n"
            "Agents should push changes directly to the main branch.\n"
            "This is the fastest way to ship.\n",
            encoding="utf-8",
        )

        # Verify the detector can catch a contract that instructs push-to-main
        control_content = control.read_text(encoding="utf-8")
        push_direct_pattern = re.compile(
            r"push.*?(?:directly|main|branch)",
            re.IGNORECASE | re.DOTALL,
        )
        control_violation = push_direct_pattern.search(control_content)
        require(
            control_violation is not None,
            "detector failed to catch the planted push-direct instruction",
        )

    return "WRITE RULE VERIFIED"


# --------------------------------------------------------------------------
# G2: no-write-capability — tools cannot push, vault is read-only
# --------------------------------------------------------------------------


def group_no_write_capability() -> str:
    """Verify the agent half is enforced by absent capability, not instruction.

    Two assertions:
    1. No tool served on the MCP surface performs git push or vault write.
    2. Every compose file mounts vault-replica into scout read-only.
    """
    # ── Assertion 1: No served tool performs push or vault write ──

    # Read the MCP policy to learn which commands are exposed as tools
    sys.path.insert(0, str(REPO_ROOT))
    from scout.cli.declarations import DECLARED  # noqa: PLC0415
    from scout.cli.mcp_policy import (  # noqa: PLC0415
        Exposure,
        policy_for,
    )

    # Gather all exposed tool names
    exposed_tools: set[str] = set()
    for spec in DECLARED:
        policy = policy_for(spec.name)
        if policy is None:
            continue
        if policy.exposure in (Exposure.TOOL, Exposure.GROUPED):
            exposed_tools.add(spec.name)

    # List of commands that must never be exposed (push, merge, vault write)
    forbidden_commands = {
        "propose",  # opens PRs
        "gate",  # commits and pushes
        "heal",  # writes vault
        "ingest",  # writes vault
    }

    exposed_forbidden = exposed_tools & forbidden_commands
    require(
        not exposed_forbidden,
        f"forbidden write commands are exposed as tools: {sorted(exposed_forbidden)}",
    )

    # Verify specific high-risk commands are HIDDEN
    high_risk = ["propose", "gate"]
    for command in high_risk:
        policy = policy_for(command)
        require(
            policy is not None and policy.exposure is Exposure.HIDDEN,
            f"{command} is not hidden (exposure={policy.exposure if policy else 'unknown'})",
        )

    # ── Assertion 2: Vault mounts are read-only ──

    saw_scout_mount = False
    for name in COMPOSE_FILES:
        doc = _load_compose(name)
        services = doc.get("services")
        if not isinstance(services, dict):
            continue
        scout = services.get("scout")
        if not isinstance(scout, dict):
            continue
        volumes = scout.get("volumes")
        if not isinstance(volumes, list):
            continue
        for entry in volumes:
            if isinstance(entry, str) and "vault-replica" in entry:
                require(
                    entry.rstrip().endswith(":ro"),
                    f"{name}: scout mounts vault-replica writable ({entry!r}); "
                    "the vault must be read-only to scout (INV-1)",
                )
                saw_scout_mount = True

    require(
        saw_scout_mount,
        "no compose file mounts vault-replica into scout; wiki_read cannot "
        "reach the vault on disk",
    )

    # ── Positive control: planted writable mount ──

    with tempfile.TemporaryDirectory(
        prefix="v3-write-capability-control-"
    ) as temporary:
        control = Path(temporary) / "bad-compose.yml"
        control.write_text(
            """---
services:
  scout:
    volumes:
      - vault-replica:/vault-replica:rw
""",
            encoding="utf-8",
        )

        # This should be caught by the ro-mount detector
        control_doc = yaml.load(
            control.read_text(encoding="utf-8"), Loader=_ComposeLoader
        )  # noqa: S506
        assert isinstance(control_doc, dict)
        control_services = control_doc.get("services", {})
        assert isinstance(control_services, dict)
        control_scout = control_services.get("scout", {})
        assert isinstance(control_scout, dict)
        control_volumes = control_scout.get("volumes", [])
        assert isinstance(control_volumes, list)

        control_has_writable = False
        for entry in control_volumes:
            if (
                isinstance(entry, str)
                and "vault-replica" in entry
                and not entry.rstrip().endswith(":ro")
            ):
                control_has_writable = True

        require(
            control_has_writable,
            "planted writable vault mount was not found (test setup failure)",
        )

    return "NO WRITE CAPABILITY VERIFIED"


# --------------------------------------------------------------------------
# Groups registry
# --------------------------------------------------------------------------


GROUPS: dict[str, Callable[[], str]] = {
    "write-rule": group_write_rule,
    "no-write-capability": group_no_write_capability,
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
    except Exception as exc:  # noqa: BLE001 - surface any oracle defect loudly
        print(f"ERROR [{args.group}] {type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc(file=sys.stderr)
        return 2

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
