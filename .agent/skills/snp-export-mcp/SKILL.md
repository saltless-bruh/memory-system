---
name: snp-export-mcp
description: "Use this skill to generate or safely merge MCP client configuration for the one SNP MCP server, the authenticated snpmemory retrieval endpoint served by Scout."
---

# Export MCP configuration

Run the shared generator through the CLI. It prints the configuration and
writes nothing:

```bash
snpmemory mcp-config --client claude
```

`--client` is required and takes one client per run: `claude`, `cursor`,
`vscode`, `gemini`, or `opencode`. For several clients, run it once per client.
To merge the result into a client's config file, name the file with `--out`;
when that file already exists, add `--confirm`, because the merge rewrites it.
Add `-o json` for a structured result an agent can parse. Those are the only
options: printing is already the default, there is no all-clients switch, and
any other flag exits `3` before anything runs.

The generated topology contains exactly one server, `snpmemory`: the
authenticated Scout endpoint on local port 8080 (`/mcp`), serving
`wiki_search`, `wiki_read`, and `wiki_quote`. OpenCode receives a native
remote entry; the other clients receive an `npx -y mcp-remote` bridge to the
same URL, which runs no SNP code and needs no checkout. There is no second,
local server: the stdio server that once held the name `snpmemory` was deleted
on 2026-09-24, and a re-export removes an old `scout` entry rather than keeping
it beside the new one. Verification and authoring stay CLI commands.

The config references `SCOUT_AUTH_HEADER` and never materializes its value.
Keep the `Bearer ` prefix in the environment value. The merge preserves servers
the SNP project does not own and removes only explicitly retired SNP-managed
entries. Never print a token while troubleshooting.

After export, list tools through an MCP client and confirm the three retrieval
tools. A plain browser request is not an MCP handshake.
