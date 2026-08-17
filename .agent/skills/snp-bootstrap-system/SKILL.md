---
name: snp-bootstrap-system
description: >-
  Use this skill when you need to deploy, restart, or bring up the entire SNP Memory System V2 infrastructure via Docker, PostgreSQL 16, and LiteLLM.
---

# snp-bootstrap-system

## Purpose
The SNP Memory System V2 relies on 6 core Docker containers (LiteLLM, PostgreSQL 16 `postgres`, `basic-memory`, `scout`, `sync-job`, `host-sync`) to provide dual-layer memory and fail-closed MCP servers. This skill guides the orchestration and verification of the system bring-up.

## How to use

1. **Verify Host Requirements**
   Ensure Docker, Docker Compose, and Python 3.12+ are installed.
   
2. **Configure API Keys**
   Ensure `.env` exists and contains required Cloud API keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`) and database passwords (`POSTGRES_PASSWORD`, `POSTGRES_APP_PASSWORD`, `LITELLM_MASTER_KEY`, `WEBHOOK_SECRET`).

3. **Run the Automated Bootstrap**
   ```bash
   ./scripts/bootstrap.sh
   ```

4. **Bring up Docker Containers**
   Build and start the entire stack in detached mode:
   ```bash
   docker compose up -d --build
   ```

5. **Verify System Health**
   - Check container status: `docker compose ps` (all 6 services healthy).
   - Check basic-memory MCP: `curl -fsS http://localhost:8765/mcp` (returns HTTP 406 for browser GET, confirming active MCP SSE endpoint).
   - Check Scout MCP: `curl -fsS http://localhost:8080/mcp` (returns HTTP 406 for browser GET).
   - Run verification gates: `python3 scripts/gen_index.py --check && uv run python scripts/verify_addresses.py`.
