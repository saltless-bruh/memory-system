"""Export MCP client configuration without materializing authentication secrets."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

CLIENT_CONFIG_PATHS: dict[str, str] = {
    "cursor": "~/.cursor/mcp.json",
    # VS Code documents a portable workspace configuration at this path. Its
    # user-profile path is platform-specific, so do not invent a ~/.vscode
    # location that VS Code will never read.
    "vscode": ".vscode/mcp.json",
    # Portable Claude Code project config. Claude Desktop remote connectors use
    # its UI/secure store and are intentionally not written by this script.
    "claude": ".mcp.json",
    "gemini": "~/.gemini/settings.json",
    "opencode": "opencode.json",
}

SUPPORTED_CLIENTS = ["cursor", "vscode", "claude", "gemini", "opencode"]

_SCOUT_URL = "http://localhost:8080/mcp"
_OPENCODE_SCOUT_URL = "http://127.0.0.1:8080/mcp"
_SCOUT_AUTH_HEADER_ENV = "SCOUT_AUTH_HEADER"
#: The name the one server is served under, everywhere a client is configured.
#: It moved here from the deleted stdio server in leaf-4.4; see
#: `scout.mcp_server.SERVER_NAME`, which this must equal and which a test holds
#: it to. It is repeated rather than imported because that module pulls in
#: fastmcp, and this script has to stay runnable on a machine with nothing
#: installed.
SERVER_NAME = "snpmemory"

#: Every name this project has ever given a server of its own.
#:
#: On merge, the ones this run is *not* generating are removed from the user's
#: config. That is the whole point and it is easy to get wrong: each rename
#: leaves the previous key behind pointing at a server that has moved or gone,
#: and the client goes on listing it. After `snp-wiki` (basic-memory, retired),
#: `snpmemory` (the local stdio server, deleted in leaf-4.3) and `scout` (this
#: same server under its old name until leaf-4.4), a config upgraded across all
#: three would otherwise carry four entries for one server.
#:
#: Deriving the removals from this set minus the generated names — rather than
#: listing the dead ones — means the next rename cannot orphan its predecessor
#: by omission. Removal runs before the generated servers are merged in, so a
#: name reused for a different server is dropped and then rewritten, never
#: shadowed.
_OWN_SERVER_NAMES = frozenset({"snp-wiki", "snpmemory", "scout"})

#: The checkout this script belongs to. Kept as the default `root` argument so
#: the signature of `generate_config` is stable for its callers, even though no
#: generated entry pins a checkout any more.
REPO_ROOT = Path(__file__).resolve().parents[1]


def _scout_config(client: str) -> dict[str, Any]:
    """Return a Scout connection containing only a secret reference.

    ``SCOUT_AUTH_HEADER`` must contain the complete HTTP header value, including
    the ``Bearer `` prefix. The exporter deliberately never reads that variable.
    """
    if client == "opencode":
        return {
            "type": "remote",
            "url": _OPENCODE_SCOUT_URL,
            "oauth": False,
            "timeout": 15000,
            "headers": {"Authorization": f"{{env:{_SCOUT_AUTH_HEADER_ENV}}}"},
        }

    config: dict[str, Any] = {
        "command": "npx",
        "args": [
            "-y",
            "mcp-remote",
            _SCOUT_URL,
            "--allow-http",
            "--header",
            f"Authorization:${{{_SCOUT_AUTH_HEADER_ENV}}}",
        ],
    }

    if client == "vscode":
        config["type"] = "stdio"

    # Cursor/VS Code and Gemini expand host environment references with
    # different syntaxes. Claude's mcp-remote process inherits the host
    # environment directly, so adding a self-referential env entry is unsafe.
    if client in {"cursor", "vscode"}:
        config["env"] = {_SCOUT_AUTH_HEADER_ENV: f"${{env:{_SCOUT_AUTH_HEADER_ENV}}}"}
    elif client == "gemini":
        config["env"] = {_SCOUT_AUTH_HEADER_ENV: f"${_SCOUT_AUTH_HEADER_ENV}"}
    return config


def generate_config(client: str, root: Path | None = None) -> dict[str, Any]:
    """Generate client configuration: one server, the authenticated Scout.

    Every client gets the same single entry. Until leaf-4.3 this also wrote a
    local stdio server pinned to a checkout; that server is deleted, so there
    is nothing left for `root` to pin.

    Args:
        client: One of `SUPPORTED_CLIENTS`.
        root: Accepted and ignored, so existing callers keep working.
    """
    del root
    if client not in SUPPORTED_CLIENTS:
        raise ValueError(f"Unknown client: {client}")
    if client == "opencode":
        return {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {SERVER_NAME: _scout_config(client)},
        }
    server_key = "servers" if client == "vscode" else "mcpServers"
    return {server_key: {SERVER_NAME: _scout_config(client)}}


def merge_configs(
    existing: dict[str, Any], new_config: dict[str, Any]
) -> dict[str, Any]:
    """Replace managed servers while retaining unrelated client settings."""
    if "mcp" in new_config:
        server_key = "mcp"
    else:
        server_key = "servers" if "servers" in new_config else "mcpServers"
    servers = new_config.get(server_key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"generated {server_key} value is not an object")

    existing_servers = existing.get(server_key, {})
    # Preserve legacy handling of null server maps; native OpenCode expects an
    # object and a malformed existing map must fail before any file is written.
    if existing_servers is None and server_key != "mcp":
        existing_servers = {}
    if not isinstance(existing_servers, dict):
        raise ValueError(f"existing {server_key} value is not an object")

    existing[server_key] = existing_servers
    # Remove our own superseded names before writing the current one, so an
    # upgraded config ends with exactly the servers this run generates plus
    # whatever the user added themselves.
    for superseded in _OWN_SERVER_NAMES - set(servers):
        existing_servers.pop(superseded, None)

    existing_servers.update(servers)
    for key, value in new_config.items():
        if key != server_key:
            existing.setdefault(key, value)
    return existing


def _prompt_for_client(parser: argparse.ArgumentParser) -> str:
    print("Select target client:")
    for index, client in enumerate(SUPPORTED_CLIENTS, 1):
        print(f"  {index}) {client}")

    try:
        choice = (
            input(f"Enter number [1-{len(SUPPORTED_CLIENTS)}] or client name: ")
            .strip()
            .lower()
        )
    except (EOFError, KeyboardInterrupt):
        parser.error("client selection aborted")

    client_map = {
        str(index): client for index, client in enumerate(SUPPORTED_CLIENTS, 1)
    }
    client = client_map.get(choice, choice)
    if client not in SUPPORTED_CLIENTS:
        parser.error(f"invalid client selection: {choice}")
    return client


class ConfigTargetConflict(ValueError):
    """A known client configuration would be shadowed by the export."""


def validate_config_target(client: str, target_path: Path) -> None:
    """Refuse to shadow an OpenCode JSONC config with conventional JSON output.

    Shared with the installer so it can check before copying any files. An
    explicitly named export artifact is allowed; a project ``opencode.json``
    requires the operator to reconcile its sibling ``opencode.jsonc`` first.
    The conflict message contains no configuration values.
    """
    if client == "opencode" and target_path.name == "opencode.json":
        sibling = target_path.with_name("opencode.jsonc")
        if sibling.exists() or sibling.is_symlink():
            raise ConfigTargetConflict(
                "existing opencode.jsonc must be reconciled before writing opencode.json"
            )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("existing config contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("existing config contains a non-JSON numeric constant")


def _finite_json_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("existing config contains an out-of-range number")
    return result


def _load_existing(target_path: Path, *, strict: bool = False) -> dict[str, Any]:
    """Load an object, optionally rejecting empty or ambiguous native JSON.

    OpenCode opts into strict parsing so blank files, duplicate keys, and
    non-finite numbers cannot be silently rewritten into a different config.
    Other clients retain their existing loader behavior.
    """
    if not target_path.exists():
        return {}

    content = target_path.read_text(encoding="utf-8")
    if not strict:
        content = content.strip()
    if not content and not strict:
        return {}
    parsed = (
        json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
        if strict
        else json.loads(content)
    )
    if not isinstance(parsed, dict):
        raise ValueError("existing config is not an object")
    return {str(key): value for key, value in parsed.items()}


def _prepare_exports(clients: Sequence[str]) -> list[tuple[str, Path, dict[str, Any]]]:
    """Read and validate every destination before any target is changed."""
    prepared: list[tuple[str, Path, dict[str, Any]]] = []
    for client in clients:
        target_path = Path(CLIENT_CONFIG_PATHS[client]).expanduser()
        validate_config_target(client, target_path)
        existing = _load_existing(target_path, strict=client == "opencode")
        merged = merge_configs(existing, generate_config(client))
        prepared.append((client, target_path, merged))
    return prepared


def _write_exports(exports: Sequence[tuple[str, Path, dict[str, Any]]]) -> None:
    snapshots = {
        target_path: target_path.read_bytes() if target_path.exists() else None
        for _, target_path, _ in exports
    }
    staged: list[tuple[str, Path, Path]] = []
    replaced: list[Path] = []
    try:
        # Fully serialize and fsync every new file before replacing any target.
        for client, target_path, config in exports:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                dir=target_path.parent,
            )
            temporary = Path(temporary_name)
            staged.append((client, target_path, temporary))
            try:
                temporary.chmod(0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    descriptor = -1
                    json.dump(config, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

        for _client, target_path, temporary in staged:
            os.replace(temporary, target_path)
            replaced.append(target_path)
    except OSError as error:
        rollback_failed = False
        for target_path in reversed(replaced):
            original = snapshots[target_path]
            try:
                if original is None:
                    target_path.unlink(missing_ok=True)
                else:
                    descriptor, temporary_name = tempfile.mkstemp(
                        prefix=f".{target_path.name}.rollback.",
                        suffix=".tmp",
                        dir=target_path.parent,
                    )
                    temporary = Path(temporary_name)
                    try:
                        temporary.chmod(0o600)
                        with os.fdopen(descriptor, "wb") as handle:
                            descriptor = -1
                            handle.write(original)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temporary, target_path)
                    finally:
                        if descriptor >= 0:
                            os.close(descriptor)
                        temporary.unlink(missing_ok=True)
            except OSError:
                rollback_failed = True
        if rollback_failed:
            raise OSError("export failed and rollback was incomplete") from error
        raise
    finally:
        for _client, _target_path, temporary in staged:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export authenticated SNP Memory System MCP configuration"
    )
    targets = parser.add_mutually_exclusive_group()
    targets.add_argument(
        "--client",
        choices=SUPPORTED_CLIENTS,
        help="Target one MCP client.",
    )
    targets.add_argument(
        "--all",
        action="store_true",
        help="Target every supported MCP client.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_config",
        help="Print configuration instead of merging it into client files.",
    )
    args = parser.parse_args(argv)

    client = args.client
    if client is None and not args.all:
        if not sys.stdin.isatty():
            parser.error("one of --client or --all is required in non-interactive mode")
        client = _prompt_for_client(parser)

    selected_clients = SUPPORTED_CLIENTS if args.all else [client]
    # The branches above guarantee a concrete client for the single-target case.
    concrete_clients = [item for item in selected_clients if item is not None]

    if args.print_config:
        if args.all:
            output: dict[str, Any] = {
                item: generate_config(item) for item in concrete_clients
            }
        else:
            output = generate_config(concrete_clients[0])
        print(json.dumps(output, indent=2))
        return 0

    try:
        exports = _prepare_exports(concrete_clients)
        _write_exports(exports)
        # Reporting belongs to this entry point, not to the writer: the CLI
        # command below renders its own result and must keep stdout clean.
        for exported_client, target_path, _config in exports:
            print(
                f"Successfully exported {exported_client} MCP config to {target_path}"
            )
    except ConfigTargetConflict as error:
        print(f"Error exporting MCP config: {error}.", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        # Error details are intentionally limited to their class: malformed
        # configs must never cause a secret-bearing value to be echoed.
        print(f"Error exporting MCP config ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
