# Connecting a Coding Agent to the SNP Memory System (V2)

> How to point **any** MCP-capable coding agent (Claude Code, Cursor, Windsurf, Cline, Gemini CLI / Antigravity, …) at this system so it can search the wiki and fetch verbatim sources. For *how the agent should behave* once connected, read **[`AGENTS.md`](../AGENTS.md)** — that is the operating contract; this file is the client wiring guide.

---

## 1. The Two MCP Endpoints

Bring the stack up first (`docker compose up -d --build`, see the [runbook](runbook.md) §3). It exposes **two Streamable HTTP MCP servers**:

| Server | URL | Transport | Tools Exposed to Agent |
|---|---|---|---|
| **basic-memory** (the wiki) | `http://localhost:8765/mcp` | Streamable HTTP | `search_notes`, `read_note`, `write_note`, … |
| **scout** (the RAG bridge) | `http://localhost:8080/mcp` | Streamable HTTP | `rag_fetch` **only** |

The agent connects to **both**:
1. `basic-memory` to navigate and read the compiled Knowledge Vault.
2. `scout` to pull the original verbatim source evidence for an address found on a page. The agent never queries PostgreSQL directly — `scout` is the fail-closed access bridge (R-4.2).

> [!NOTE]
> A plain browser GET request to `http://localhost:8765/mcp` or `http://localhost:8080/mcp` returns **HTTP 406 Not Acceptable**. This is expected behavior under the MCP Streamable HTTP specification: the endpoint requires MCP protocol headers and SSE streaming initiated by an MCP client.

---

## 2. Per-Client Configuration Templates

### A. Claude Code CLI

```bash
claude mcp add --transport http snp-wiki http://localhost:8765/mcp
claude mcp add --transport http scout    http://localhost:8080/mcp
```

Inside a Claude Code session, run `/mcp` to verify that `snp-wiki` and `scout` tools are active.

---

### B. Cursor / Windsurf / Cline (`.mcp.json` or Workspace Settings)

Save the following in `.mcp.json` in your project root or add to your global MCP settings:

```json
{
  "mcpServers": {
    "snp-wiki": {
      "url": "http://localhost:8765/mcp"
    },
    "scout": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

---

### C. Gemini CLI / Antigravity (`~/.gemini/settings.json`)

```json
{
  "mcpServers": {
    "snp-wiki": {
      "httpUrl": "http://localhost:8765/mcp"
    },
    "scout": {
      "httpUrl": "http://localhost:8080/mcp"
    }
  }
}
```

---

### D. Stdio-Only MCP Clients (`mcp-remote` Bridge)

Clients that only launch MCP servers as subprocesses (stdio) can bridge to HTTP endpoints using [`mcp-remote`](https://www.npmjs.com/package/mcp-remote):

```json
{
  "mcpServers": {
    "snp-wiki": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8765/mcp"]
    },
    "scout": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8080/mcp"]
    }
  }
}
```

---

## 3. One-Line Smart Package Installer (Automated Setup)

Instead of manually creating `.mcp.json` and copying rules, you can bootstrap the entire SNP agent package into any workspace:

```bash
curl -fsSL https://raw.githubusercontent.com/saltless-bruh/memory-system/main/scripts/install-agent.sh | bash
```

This non-destructively installs the SNP rules (`rules/snp-memory.md`), 6 dual-mode slash-command workflows (`/snp-query`, `/snp-compile`, etc.), and 8 progressive disclosure skills into `.agent/`.

---

## 4. Automated Configuration Export

To generate copy-pasteable JSON configuration blocks for your current system automatically, run:

```bash
python scripts/export_mcp_config.py
```

---

## 5. Verifying Agent Connectivity & Handshake

Once configured, perform the handshake in your agent chat:

1. **Trigger Handshake**:
   Type `/snp-reload` in your chat session $\rightarrow$ the agent loads all rules, skills, and verifies endpoint connectivity.
2. **Test Wiki Search**:
   Call `basic-memory.search_notes("PagedAttention Engine")` $\rightarrow$ returns the note with summary and tags.
3. **Test RAG Fetch**:
   Call `Scout.rag_fetch(path="raw/reports/vllm_high_throughput_serving.pdf", hint="PagedAttention KV-Cache Virtual Block Allocation")` $\rightarrow$ returns `status: "ok"` with verbatim `context[]` and `citations[]`.
