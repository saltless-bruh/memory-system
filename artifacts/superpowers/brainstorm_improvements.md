# Brainstorming Improvements for SNP Memory System

**Date:** August 4, 2026  
**Goal:** Identify high-value feature enhancements, developer experience upgrades, and automation tools to make the SNP Memory System faster, smarter, and easier to maintain.

---

## 1. Goal, Constraints & Acceptance Criteria

### Goal
Propose actionable, high-ROI architectural and tooling improvements for the SNP Memory System across three categories:
1. **Automation & DX (Developer/Agent Experience)**
2. **Knowledge Graph & Navigation**
3. **System Reliability & Continuous Quality**

### Constraints
- Must adhere to the strict **No-Egress** security boundary (LiteLLM → Local Ollama).
- Must preserve the **2-Tier Architecture** (Wiki Knowledge Vault = map; RAG Data Vault = verbatim warehouse).
- Must maintain 100% compliance with `AGENTS.md` contracts and zero-error vault linting (`gen_index.py`).

### Acceptance Criteria
- Deliver 5 concrete, prioritized proposal specs with clear ROI, implementation effort, and step-by-step verification paths.

---

## 2. Proposed Improvements & Features

### Proposal 1: Automated Wiki Page Compiler (`scripts/compile_note.py`)
* **Problem**: Writing a compliant wiki note from a raw source requires manual frontmatter formatting, address minting (`mint.py`), section structuring, and wikilink resolution.
* **Solution**: Create a CLI tool `scripts/compile_note.py --path raw/rfcs/rfc1035.md --title "Domain Names"` that:
  1. Uses LiteLLM (`qwen3.5:2b` / `qwen2.5:7b-instruct`) to generate a 1-sentence summary and extract entities.
  2. Calls `scripts/mint.py` programmatically to mint valid `sources[]` addresses.
  3. Generates body sections (`## TL;DR`, `## Technical Specifications`, `## Provenance`, `## Cross-References`).
  4. Runs `gen_index.py` and opens a PR branch (`propose_page.py`).
* **ROI**: Reduces wiki page compilation time from 15 minutes to **30 seconds**.

---

### Proposal 2: Interactive Knowledge Graph Visualizer (`scripts/graph_viz.py`)
* **Problem**: As `wiki/` grows to hundreds of notes, tracking concept clusters, orphan notes, and knowledge gaps textually is difficult.
* **Solution**: Build `scripts/graph_viz.py` that parses `[[wikilink]]` references across `wiki/` and generates:
  - An interactive Mermaid.js diagram embedded into `wiki/index.md`.
  - An standalone HTML Knowledge Graph visualizer for team dashboards.
* **ROI**: Provides immediate visual clarity on knowledge density, orphan pages, and unlinked concepts.

---

### Proposal 3: Continuous Address Drift Auto-Healer (`scout/healer.py`)
* **Problem**: Over time, re-indexing raw files in RAG-Anything might cause `sources[].hint` phrases to drift or fail retrieval.
* **Solution**: Extend `scout.sync_job` to periodically run `verify_addresses.py` in the background. When address drift is detected:
  - Log the drift to `wiki/log.md`.
  - Automatically re-mint the address using `mint.py` and suggest an updated frontmatter patch.
* **ROI**: Zero broken citations across long-term raw data re-indexing.

---

### Proposal 4: One-Click Agent MCP Configuration Exporter (`scripts/export_mcp_config.py`)
* **Problem**: Wiring different IDEs (Claude Code, Cursor, Windsurf, Gemini CLI, VS Code) requires manually editing JSON/TOML configuration files.
* **Solution**: Create `scripts/export_mcp_config.py --client [cursor|claude|gemini|codex|vscode]` that automatically detects the IDE config path and injects the live streamable-HTTP endpoints (`:8765` for `basic-memory`, `:8080` for `scout`).
* **ROI**: One-command agent setup for any IDE or CLI tool.

---

### Proposal 5: Incremental Vector Caching for `ScoutDiyEngine`
* **Problem**: Full-vault summary re-embedding on every `wiki_search` startup scales linearly with vault size.
* **Solution**: Implement disk-backed SQLite/JSON vector caching in `ScoutDiyEngine` keyed by file SHA-256 hash so only modified/new wiki notes are re-embedded.
* **ROI**: Sub-millisecond startup times for `wiki_search` even with 10,000+ wiki pages.

---

## 3. Prioritized Implementation Roadmap

| Proposal | Category | Effort | Value / ROI | Sprint Target |
| :--- | :--- | :---: | :---: | :---: |
| **1. One-Click MCP Config Exporter** | DX / Setup | 2 hrs | **High** | Sprint 1 |
| **2. Automated Wiki Page Compiler** | Automation | 4 hrs | **Very High** | Sprint 1 |
| **3. Knowledge Graph Visualizer** | Navigation | 3 hrs | **High** | Sprint 2 |
| **4. Incremental Vector Cache** | Performance | 3 hrs | **Medium** | Sprint 2 |
| **5. Continuous Address Auto-Healer** | Quality | 5 hrs | **High** | Sprint 3 |

---

## 4. Next Action & Verification Plan

1. **User Selection**: Review the 5 proposals and select which proposal(s) you would like to implement first.
2. **Execution Gate**: After selecting a proposal, obtain user approval on the implementation plan and proceed with TDD development and verification tests.
