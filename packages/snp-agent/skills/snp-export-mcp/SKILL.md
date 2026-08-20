---
name: snp-export-mcp
description: >-
  Use this skill when you are asked to help a user connect their AI coding agent or editor (like Claude, Cursor, or Gemini) to the SNP Memory System.
---

# snp-export-mcp

## Purpose
The SNP Memory System exposes two independent MCP endpoints (`basic-memory` at port 8765, and `scout` at port 8080). This skill teaches you how to generate the correct client configuration JSON so a new user can instantly plug their agent into the system.

## How to use

1. **Run the Exporter Script**
   Use the `scripts/export_mcp_config.py` script and pass the specific `--client` the user is asking for (e.g., `cursor`, `claude`, `gemini`, `vscode`). 
   Use the `--print` flag to output the JSON to standard out.
   
   ```bash
   python scripts/export_mcp_config.py --client cursor --print
   ```
   Use `--all --print` to preview all clients, or omit `--print` to merge
   managed entries into the target file. Non-interactive use requires
   `--client` or `--all`.

   Set `SCOUT_AUTH_HEADER='Bearer <token>'` in the client's runtime
   environment. Generated JSON contains only the environment reference; the
   exporter never reads or prints its value.

   VS Code writes the portable workspace file `.vscode/mcp.json` with its
   native top-level `servers` schema. The `claude` target writes the portable
   Claude Code project file `.mcp.json`; it does not target Claude Desktop.
   Cursor, Claude Code, and Gemini use `mcpServers` in their documented
   destinations.
   
2. **Provide Instructions to the User**
   Copy the generated JSON output and present it to the user.
   Tell the user exactly which file the exporter targets. For Claude Desktop,
   direct the user to its current Connectors/Extensions UI instead of claiming
   that the Claude Code `.mcp.json` file configures Desktop.

3. **Verify Connectivity**
   Test basic-memory search and an authenticated Scout fetch. A missing or
   unauthorized bearer token must be rejected.
