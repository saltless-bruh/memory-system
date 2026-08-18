import argparse
import json
import sys
from pathlib import Path
from typing import Any

CLIENT_CONFIG_PATHS: dict[str, str] = {
    "cursor": "~/.cursor/mcp.json",
    "vscode": "~/.vscode/mcp.json",
    "claude": "~/.claude/claude_desktop_config.json",
    "gemini": "~/.gemini/settings.json",
}


def generate_config(client: str) -> dict[str, Any]:
    if client in ["cursor", "vscode"]:
        return {
            "mcpServers": {
                "snp-wiki": {"type": "sse", "url": "http://localhost:8765/mcp"},
                "scout": {"type": "sse", "url": "http://localhost:8080/mcp"},
            }
        }
    elif client == "gemini":
        return {
            "mcpServers": {
                "snp-wiki": {"httpUrl": "http://localhost:8765/mcp"},
                "scout": {"httpUrl": "http://localhost:8080/mcp"},
            }
        }
    elif client == "claude":
        return {
            "mcpServers": {
                "snp-wiki": {
                    "command": "npx",
                    "args": ["-y", "mcp-remote", "http://localhost:8765/mcp"],
                },
                "scout": {
                    "command": "npx",
                    "args": ["-y", "mcp-remote", "http://localhost:8080/mcp"],
                },
            }
        }
    else:
        raise ValueError(f"Unknown client: {client}")


def merge_configs(
    existing: dict[str, Any], new_config: dict[str, Any]
) -> dict[str, Any]:
    if "mcpServers" not in existing:
        existing["mcpServers"] = {}

    servers = new_config.get("mcpServers", {})
    if not isinstance(servers, dict):
        return existing

    for server_name, server_config in servers.items():
        existing["mcpServers"][server_name] = server_config

    return existing


SUPPORTED_CLIENTS = ["cursor", "vscode", "claude", "gemini"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-Click Agent MCP Configuration Exporter"
    )
    parser.add_argument(
        "--client",
        choices=SUPPORTED_CLIENTS,
        required=False,
        default=None,
        help="The target client for the MCP configuration.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the configuration to stdout instead of merging into file.",
    )
    args = parser.parse_args()

    client = args.client

    if client is None:
        if args.print:
            all_configs = {c: generate_config(c) for c in SUPPORTED_CLIENTS}
            print(json.dumps(all_configs, indent=2))
            return

        if sys.stdin.isatty():
            print("Select target client:")
            for idx, c in enumerate(SUPPORTED_CLIENTS, 1):
                print(f"  {idx}) {c}")
            try:
                choice = input("Enter number [1-4] or client name: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.", file=sys.stderr)
                sys.exit(1)

            client_map = {str(i): c for i, c in enumerate(SUPPORTED_CLIENTS, 1)}
            if choice in client_map:
                client = client_map[choice]
            elif choice in SUPPORTED_CLIENTS:
                client = choice
            else:
                print(f"Error: Invalid client selection '{choice}'.", file=sys.stderr)
                sys.exit(1)
        else:
            all_configs = {c: generate_config(c) for c in SUPPORTED_CLIENTS}
            print(json.dumps(all_configs, indent=2))
            return

    config = generate_config(client)

    if args.print:
        print(json.dumps(config, indent=2))
        return

    path_str = CLIENT_CONFIG_PATHS[client]
    target_path = Path(path_str).expanduser()

    existing_config: dict[str, Any] = {}
    if target_path.exists():
        try:
            with open(target_path, encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        existing_config = {str(k): v for k, v in parsed.items()}
                    else:
                        print(
                            f"Error: Existing config at {target_path} "
                            "is not an object.",
                            file=sys.stderr,
                        )
                        sys.exit(1)
        except json.JSONDecodeError:
            print(f"Error reading JSON config at {target_path}.", file=sys.stderr)
            sys.exit(1)

    merged_config = merge_configs(existing_config, config)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(merged_config, f, indent=2)
        f.write("\n")

    print(f"Successfully exported {client} MCP config to {target_path}")


if __name__ == "__main__":
    main()
