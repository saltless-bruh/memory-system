"""Export MCP client configuration without materializing authentication secrets."""

from __future__ import annotations

import argparse
import json
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
}

SUPPORTED_CLIENTS = ["cursor", "vscode", "claude", "gemini"]

_BASIC_MEMORY_URL = "http://localhost:8765/mcp"
_SCOUT_URL = "http://localhost:8080/mcp"
_SCOUT_AUTH_HEADER_ENV = "SCOUT_AUTH_HEADER"

#: The checkout this script belongs to — the default for the local server's
#: pinned root, since a config exported from a clone should serve that clone.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Must equal `scout.mcp.local_server.SERVER_NAME`. It is repeated rather than
#: imported because that module pulls in fastmcp, and this script has to stay
#: runnable on a machine with nothing installed. A test holds the two together.
LOCAL_SERVER_NAME = "snpmemory"

#: Installed by `[project.scripts]`, so the exported entry can name a command on
#: PATH rather than a path into a checkout the user may not have.
_LOCAL_SERVER_COMMAND = "snpmemory"


def _basic_memory_config(client: str) -> dict[str, Any]:
    """Return the project's existing, unauthenticated basic-memory config."""
    if client == "cursor":
        return {"type": "sse", "url": _BASIC_MEMORY_URL}
    if client == "vscode":
        return {"type": "http", "url": _BASIC_MEMORY_URL}
    if client == "gemini":
        return {"httpUrl": _BASIC_MEMORY_URL}
    return {
        "command": "npx",
        "args": ["-y", "mcp-remote", _BASIC_MEMORY_URL],
    }


def _scout_config(client: str) -> dict[str, Any]:
    """Return a Scout bridge config containing only a secret reference.

    ``SCOUT_AUTH_HEADER`` must contain the complete HTTP header value, including
    the ``Bearer `` prefix. The exporter deliberately never reads that variable.
    """
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


def _local_server_config(client: str, root: Path) -> dict[str, Any]:
    """Return the local stdio server entry, with the checkout it serves pinned.

    The root travels in ``args`` rather than in a client-specific ``cwd`` key:
    argv is the one field every one of these four clients passes through
    unchanged, and an operator reading the config can see which checkout is
    being served instead of having to know what directory the client launches
    in. `snpmemory mcp` resolves relative paths and `.env` against its working
    directory, so an unpinned launch would serve whichever tree the client
    happened to start from — or none at all.

    No token and no network address: this server is stdio-only and carries the
    authority of whoever launches it. See `scout/mcp/local_server.py`.
    """
    config: dict[str, Any] = {
        "command": _LOCAL_SERVER_COMMAND,
        "args": ["mcp", "--root", str(root)],
    }
    if client == "vscode":
        config["type"] = "stdio"
    return config


def generate_config(client: str, root: Path | None = None) -> dict[str, Any]:
    """Generate a client config for all three servers this system offers.

    Args:
        client: One of `SUPPORTED_CLIENTS`.
        root: The checkout the local stdio server should serve. Defaults to the
            one this script lives in.
    """
    if client not in SUPPORTED_CLIENTS:
        raise ValueError(f"Unknown client: {client}")
    server_key = "servers" if client == "vscode" else "mcpServers"
    return {
        server_key: {
            "snp-wiki": _basic_memory_config(client),
            "scout": _scout_config(client),
            LOCAL_SERVER_NAME: _local_server_config(client, root or REPO_ROOT),
        }
    }


def merge_configs(
    existing: dict[str, Any], new_config: dict[str, Any]
) -> dict[str, Any]:
    """Replace managed servers while retaining unrelated client settings."""
    server_key = "servers" if "servers" in new_config else "mcpServers"
    existing_servers = existing.get(server_key)
    if existing_servers is None:
        existing_servers = {}
        existing[server_key] = existing_servers
    if not isinstance(existing_servers, dict):
        raise ValueError(f"existing {server_key} value is not an object")

    servers = new_config.get(server_key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"generated {server_key} value is not an object")
    existing_servers.update(servers)
    return existing


def _prompt_for_client(parser: argparse.ArgumentParser) -> str:
    print("Select target client:")
    for index, client in enumerate(SUPPORTED_CLIENTS, 1):
        print(f"  {index}) {client}")

    try:
        choice = input("Enter number [1-4] or client name: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        parser.error("client selection aborted")

    client_map = {
        str(index): client for index, client in enumerate(SUPPORTED_CLIENTS, 1)
    }
    client = client_map.get(choice, choice)
    if client not in SUPPORTED_CLIENTS:
        parser.error(f"invalid client selection: {choice}")
    return client


def _load_existing(target_path: Path) -> dict[str, Any]:
    if not target_path.exists():
        return {}

    content = target_path.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("existing config is not an object")
    return {str(key): value for key, value in parsed.items()}


def _prepare_exports(clients: Sequence[str]) -> list[tuple[str, Path, dict[str, Any]]]:
    """Read and validate every destination before any target is changed."""
    prepared: list[tuple[str, Path, dict[str, Any]]] = []
    for client in clients:
        target_path = Path(CLIENT_CONFIG_PATHS[client]).expanduser()
        existing = _load_existing(target_path)
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
                os.fchmod(descriptor, 0o600)
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
                        os.fchmod(descriptor, 0o600)
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
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        # Error details are intentionally limited to their class: malformed
        # configs must never cause a secret-bearing value to be echoed.
        print(f"Error exporting MCP config ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
