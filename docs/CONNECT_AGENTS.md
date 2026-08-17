# Connecting a Coding Agent to the SNP Memory System

> How to point **any** MCP-capable coding agent (Claude Code, Codex, Cursor,
> Cline, Gemini / Antigravity, …) at this system so it can search the wiki and
> fetch verbatim sources. For *how the agent should behave* once connected,
> read **[`AGENTS.md`](../AGENTS.md)** — that is the operating contract; this
> file is only the wiring.

## The two endpoints (the invariant facts)

Bring the stack up first (`docker compose up -d --build`, see the
[runbook](runbook.md) §3). It then exposes **two MCP servers** over
**streamable-HTTP**:

| Server | URL | Transport | Tools the agent gets |
|---|---|---|---|
| **basic-memory** (the wiki) | `http://localhost:8765/mcp` | streamable-http | `search_notes`, `read_note`, `write_note`, … |
| **scout** (the RAG bridge) | `http://localhost:8080/mcp` | streamable-http | `rag_fetch` **only** |

That is the whole surface. The agent connects to **both**: `basic-memory` to
navigate/read the compiled wiki, `scout` to pull the original verbatim source
for an address it found on a page. The agent can **never** reach RAG-Anything
directly — `scout` is the only door (R-4.2), and it is the only thing on the
Docker network that can (the `rag` service publishes no port).

> A plain browser GET to `/mcp` returns **HTTP 406** — that is correct, not a
> failure. The endpoint requires MCP protocol headers; only an MCP client
> speaks to it properly.

## Per-client setup

The URLs + transport above are the same everywhere; only the config *format*
differs. Two of these clients speak HTTP-MCP natively; stdio-only clients need
the one-line `mcp-remote` bridge (bottom).

### Claude Code

```bash
claude mcp add --transport http snp-wiki  http://localhost:8765/mcp
claude mcp add --transport http scout     http://localhost:8080/mcp
```

Then in a session, `/mcp` lists both servers and their tools.

### Cursor / Cline / Windsurf (`.mcp.json` / `mcpServers`)

```json
{
  "mcpServers": {
    "snp-wiki": { "url": "http://localhost:8765/mcp" },
    "scout":    { "url": "http://localhost:8080/mcp" }
  }
}
```

Some builds key the URL as `"serverUrl"` or expect `"type": "streamable-http"`
alongside `"url"` — check your client's MCP docs if it doesn't pick it up.

### Gemini CLI / Antigravity (`~/.gemini/settings.json`)

```json
{
  "mcpServers": {
    "snp-wiki": { "httpUrl": "http://localhost:8765/mcp" },
    "scout":    { "httpUrl": "http://localhost:8080/mcp" }
  }
}
```

Gemini uses `httpUrl` for streamable-HTTP servers (older versions: `url`).

### Codex CLI and other stdio-only clients (`mcp-remote` bridge)

Clients that only launch MCP servers as a subprocess (stdio) can't hold an
HTTP URL directly. Bridge with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)
(needs Node):

```toml
# ~/.codex/config.toml
[mcp_servers.snp-wiki]
command = "npx"
args = ["-y", "mcp-remote", "http://localhost:8765/mcp"]

[mcp_servers.scout]
command = "npx"
args = ["-y", "mcp-remote", "http://localhost:8080/mcp"]
```

The same `command`/`args` shape works for any stdio-only MCP client (just its
own config file/keys). If your Codex build supports HTTP MCP natively, use its
`url` form instead of the bridge.

## Verify the connection

Once wired, ask the agent to list tools — you should see `search_notes` +
`read_note` (from `snp-wiki`) and `rag_fetch` (from `scout`). A fast smoke
test, in the agent:

1. `search_notes("kerberoasting")` → returns `techniques/kerberoasting.md`.
2. `read_note("techniques/kerberoasting.md")` → body + `sources[]`.
3. `rag_fetch(path="raw/reports/acme-2026-final.pdf", hint="Acme kerberoasting service account SPN offline crack")`
   → `status: "ok"` with verbatim `context[]` + `citations[]`.

The full scripted demo is in **[`DEMO.md`](DEMO.md)**.

## The no-egress caveat (read this before a sensitive demo)

The **system** is local-only: every model call it makes (embeddings, entity
extraction, VLM parsing) goes through LiteLLM → local Ollama, no internet
(runbook §1). **But the agent's own model is outside that boundary.** If you
connect a cloud agent (Claude Code, Codex, Gemini on their hosted models), the
wiki text and RAG passages the agent *reads to answer* travel to that
provider. For absolute no-egress, the member must run a **local** agent model.
This is a property of the member's IDE, not something the system can enforce.
