---
description: Hot-reloads SNP Memory System rules, skills, workflows, and verifies MCP endpoint connectivity.
---

# /snp-reload

Execute the following synchronization protocol:

1. **Rule Indexing**:
   - Read `.agent/rules/snp-memory.md`. Confirm strict adherence to Rule R-5 (Wiki first, RAG second), Rule R-8.5 (Prompt Injection Neutralization), and Rule R-6.4 (PR-First commits).

2. **Workflows & Skills Enumeration**:
   - Enumerate all slash commands in `.agent/workflows/` (`/snp-query`, `/snp-compile`, `/snp-ingest`, `/snp-verify`, `/snp-heal`, `/snp-reload`).
   - Enumerate all active domain skills in `.agent/skills/`.

3. **Topology & Connectivity Handshake**:
   - Read non-secret configuration references from `.env` / `.mcp.json`.
   - Confirm the client runtime provides
     `SCOUT_AUTH_HEADER='Bearer <token>'`; never print its value.
   - Probe `basic-memory` endpoint (`http://localhost:8765/mcp`).
   - Probe authenticated Scout at `http://localhost:8080/mcp` using the MCP
     client; do not assume an unimplemented remote endpoint.

4. **Output Operational Readiness Card**:
   - Output structured status card confirming loaded rules, active skills,
     authenticated local endpoints, and readiness status:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 🧠 SNP Memory System Agent Environment Initialized (V2)                │
├────────────────────────────────────────────────────────────────────────┤
│ • Rules Loaded: snp-memory.md (R-5, R-8.5, R-6.3, R-6.4)              │
│ • Active Skills: 8 skills loaded from .agent/skills/                   │
│ • Active Workflows: /snp-query, /snp-compile, /snp-ingest, ...         │
│ • Knowledge Vault: basic-memory (:8765)                                │
│ • Data Vault Bridge: authenticated Scout (:8080)                       │
│ Status: READY FOR ASSISTED RETRIEVAL & NOTE COMPILING                  │
└────────────────────────────────────────────────────────────────────────┘
```
