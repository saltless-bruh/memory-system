---
name: snp-export-mcp
description: "Use this skill to generate or safely merge MCP client configuration for the authenticated Scout retrieval server and the local snpmemory stdio server."
---

# Export MCP configuration

Run the shared generator through the CLI:

```bash
snpmemory mcp-config --client claude
```

Use `--client cursor`, `--client vscode`, or `--client gemini` for another
supported client, and `--all` only when every configured destination is
intended. Add `--print` to inspect output without writing it.

The generated topology contains exactly:

- `scout`, an authenticated Streamable HTTP connection serving `wiki_search`
  and `wiki_read`;
- `snpmemory`, a local stdio connection serving the same retrieval pair plus
  verification and authoring tools.

The config references `SCOUT_AUTH_HEADER` and never materializes its value.
Keep the `Bearer ` prefix in the environment value. The merge preserves servers
the SNP project does not own and removes only explicitly retired SNP-managed
entries. Never print a token while troubleshooting.

After export, list tools through an MCP client and confirm the retrieval pair.
A plain browser request is not an MCP handshake.
