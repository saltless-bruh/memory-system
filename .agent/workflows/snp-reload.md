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
   - Read configuration from `.env` / `.mcp.json`.
   - Probe `basic-memory` endpoint (`http://localhost:8765/mcp`).
   - Probe `Scout` endpoint (Local: `http://localhost:8080/mcp` | Team: `https://scout.snp.internal/mcp`).

4. **Output Operational Readiness Card**:
   - Output structured status card confirming loaded rules, active skills, detected topology (Local vs. Team), and readiness status:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 🧠 SNP Memory System Agent Environment Initialized (V2)                │
├────────────────────────────────────────────────────────────────────────┤
│ • Rules Loaded: snp-memory.md (R-5, R-8.5, R-6.3, R-6.4)              │
│ • Active Skills: 8 skills loaded from .agent/skills/                   │
│ • Active Workflows: /snp-query, /snp-compile, /snp-ingest, ...         │
│ • Knowledge Vault: basic-memory (:8765)                                │
│ • Data Vault Bridge: Scout (:8080 or Remote)                           │
│ Status: READY FOR ASSISTED RETRIEVAL & NOTE COMPILING                  │
└────────────────────────────────────────────────────────────────────────┘
```
