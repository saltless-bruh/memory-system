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
   
2. **Provide Instructions to the User**
   Copy the generated JSON output and present it to the user.
   Tell the user exactly which file they need to paste this JSON into based on their editor (e.g., `~/.claude/claude_desktop_config.json` for Claude Desktop).

3. **Verify Connectivity**
   Once the user updates their config, ask them to test the connection by asking you to search the wiki using the `basic-memory` MCP.
