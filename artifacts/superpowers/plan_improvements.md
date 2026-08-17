# Implementation Plan: SNP Memory System Enhancements

**Goal:** Implement the 4 requested proposals for the SNP Memory System:
- **Proposal 1**: Automated Wiki Page Compiler (`scripts/compile_note.py`)
- **Proposal 3**: Continuous Address Drift Auto-Healer (`scout/healer.py`)
- **Proposal 4**: One-Click Agent MCP Configuration Exporter (`scripts/export_mcp_config.py`)
- **Proposal 5**: Incremental Vector Caching for `ScoutDiyEngine` (`scout/diy_engine.py`)

*(Excluded: Proposal 2 - Interactive Knowledge Graph Visualizer as Obsidian natively handles graph visualization)*

---

## Step-by-Step Implementation Plan

### Step 1: Automated Wiki Page Compiler (`scripts/compile_note.py`)
- **Files to create/modify**: `scripts/compile_note.py`, `tests/test_compile_note.py`
- **Key Mechanics**:
  - Accept command-line arguments: `--path raw/...`, `--title "..."`, `--category concept|technique|entity|playbook`.
  - Extract document content, single-sentence summary, and key entities.
  - Automatically call `mint_address()` from `scripts/mint.py` to mint verifiable `sources[]` blocks.
  - Construct fully-compliant Markdown note with mandatory sections (`TL;DR`, `Technical Specifications`, `Provenance`, `Cross-References`).
  - Run `scripts/gen_index.py` to regenerate `wiki/index.md`.
- **Verification Command**:
  ```bash
  python3 scripts/compile_note.py --path raw/rfcs/rfc791-ipv4.md --title "IPv4 Test Note" --category concept
  python3 scripts/gen_index.py --check
  uv run pytest tests/test_compile_note.py
  ```

---

### Step 2: Continuous Address Drift Auto-Healer (`scout/healer.py`)
- **Files to create/modify**: `scout/healer.py`, `scout/sync_job.py`, `tests/test_healer.py`
- **Key Mechanics**:
  - Implement `verify_and_heal_vault()` to audit `sources[]` pointers using `verify_addresses.py`.
  - When address DRIFT or FAIL is detected, call `mint_address()` from `scripts/mint.py` to attempt auto-minting an updated passing hint.
  - Write healing logs to `wiki/log.md`.
- **Verification Command**:
  ```bash
  uv run pytest tests/test_healer.py
  ```

---

### Step 3: One-Click Agent MCP Configuration Exporter (`scripts/export_mcp_config.py`)
- **Files to create/modify**: `scripts/export_mcp_config.py`, `tests/test_export_mcp_config.py`
- **Key Mechanics**:
  - Detect installed client tools / IDEs (`cursor`, `claude`, `gemini`, `vscode`).
  - Export or merge standard MCP server definitions into target client config files for `basic-memory` (`http://localhost:8765/mcp`) and `scout` (`http://localhost:8080/mcp`).
  - Support `--print` mode to display JSON/TOML configuration without modifying files.
- **Verification Command**:
  ```bash
  python3 scripts/export_mcp_config.py --print
  uv run pytest tests/test_export_mcp_config.py
  ```

---

### Step 4: Incremental Vector Caching for `ScoutDiyEngine` (`scout/diy_engine.py`)
- **Files to modify**: `scout/diy_engine.py`, `tests/test_diy_engine.py`
- **Key Mechanics**:
  - Add SHA-256 content hashing for wiki page summaries.
  - Persist vector cache to `.basic-memory/vector_cache.json`.
  - Skip re-embedding unchanged notes during `_ensure_index()`.
- **Verification Command**:
  ```bash
  uv run pytest tests/test_diy_engine.py
  ```

---

## Final Verification Checklist

- [ ] All 126 existing unit tests pass cleanly: `uv run pytest`
- [ ] New unit tests pass for Compiler, Auto-Healer, MCP Exporter, and Vector Cache.
- [ ] Vault linter passes: `python3 scripts/gen_index.py --check`
- [ ] Strict type checking passes: `uv run mypy scout scripts`
- [ ] Code formatting clean: `uv run ruff check .`
