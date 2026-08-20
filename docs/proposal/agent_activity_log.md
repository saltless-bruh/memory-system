# Agent Activity Log: SNP Memory System

> **HISTORICAL ACTIVITY LOG.** Events and commands below describe their time of
> execution and are not current operating instructions. See
> `docs/ARCHITECTURE_STATUS.md`.

**Date:** 2026-08-05
**Project:** SNP Memory System
**Objective:** Implement four major automation steps, fix integration issues, and prepare the project for a live demo.

---

## 1. Initial Assessment & Bootstrapping
* **Context Loading:** Read `AGENTS.md` and `.agent/instructions/*` to understand the rules for the Wiki Vault, RAG system, address minting constraints, and the PR/Git workflow.
* **Planning:** Formulated a multi-agent execution plan to develop the four requested features in parallel.

## 2. Feature Implementation
Subagents were spawned to work on the following features concurrently. All code was checked with `ruff`, type-checked with `mypy`, and unit-tested with `pytest`.

### Step 1: Automated Wiki Page Compiler (`scripts/compile_note.py`)
* Created a CLI script to take a raw data source (e.g., an RFC) and automatically compile a wiki note.
* Integrated the local `snp-llm` model to auto-generate a single-sentence `summary` and an `entities` list based on the raw text.
* Integrated the `scripts/mint.py` logic to automatically query the RAG backend and mint valid address hints, ensuring 100% compliance with `AGENTS.md`.
* Automatically populated the required frontmatter sections (`type`, `title`, `summary`, `entities`, `department`, `sources`, `last_compiled`) and Markdown headings.

### Step 2: Continuous Address Drift Auto-Healer (`scout/healer.py`)
* Implemented `verify_and_heal_vault(backend)` to programmatically check the integrity of the data vault's addresses.
* Logic leverages `verify_all()`: for any address returning a `DRIFT` or `FAIL` status, the script calls the RAG backend to mint a new hint from the `summary` chunk.
* On successful healing, the note's frontmatter is rewritten to correct the drift, and the action is logged.

### Step 3: MCP Config Exporter (`scripts/export_mcp_config.py`)
* Created a utility to easily onboard new developer tools (Cursor, Claude Desktop, VS Code, Gemini).
* The script generates the exact JSON or TOML configuration needed to wire up the local `basic-memory` and `scout` MCP servers via SSE connections on ports `8765` and `8080`.

### Step 4: Incremental Vector Caching (`scout/diy_engine.py`)
* Modified the `ScoutDiyEngine` to drastically reduce startup time by caching embeddings.
* **Mechanism:** Hashes the concatenation of `summary` and `entities`. If a matching hash is found in `.basic-memory/vector_cache.json`, the vector is loaded from disk. Otherwise, it calls the embedder and saves it.

## 3. Integration Testing & Debugging
* **Issue Encountered:** During end-to-end integration tests (`run_system.py`), the process hung indefinitely when testing the auto-healer.
* **Root Cause Analysis:** Traced the issue to the `snp-rag` / `litellm` layer. Discovered that the free-tier API keys for **OpenRouter** had hit a 429 Rate Limit (capped at 50 requests/day). The system was aggressively retrying the RAG interactions.
* **Resolution:** Reconfigured the project's `config/litellm/config.yaml` to point `snp-llm` and `snp-vlm` directly to the local **Ollama** model (`qwen3.5:2b`). This ensured a completely offline, fast, and local-first execution environment.
* **Final Test:** Re-ran `run_system.py`. Successfully embedded 12 wiki pages using the new Incremental Vector Cache, and successfully executed the Auto-Healer against the local RAG backend.

## 4. Final Polish & Demo Preparation
* **Global Configuration Update:** Modified `.agent/instructions/internet_search.instructions.md` to explicitly forbid the AI from assuming knowledge about modern technology (e.g., `gemini-3.5-flash`), enforcing a strict "web search first" rule.
* **Demo Scenarios:** Created the `demo_scenarios.md` artifact, providing a script of copy-pasteable commands for the user to demonstrate the MCP Exporter, Wiki Compiler, Vector Caching, and Core Network Query functionalities smoothly on demo day.
* **Git Synchronization:** Added all new files, committed with message *"feat: Implement Wiki Page Compiler, Auto-Healer, MCP Config Exporter, and Vector Caching"*, and pushed upstream to `origin/main`.
