---
name: snp-bootstrap-system
description: >-
  Use this skill when you need to deploy, restart, or bring up the entire SNP Memory System infrastructure via Docker and LiteLLM.
---

# snp-bootstrap-system

## Purpose
The SNP Memory System relies on 6 Docker containers (RAG engines, basic-memory, scout) and a LiteLLM proxy pointing to Cloud APIs (Claude, OpenAI, Gemini) to maintain its AI capabilities. This skill teaches you how to orchestrate the bring-up.

## How to use

1. **Verify Host Requirements**
   Ensure Docker and Docker Compose are installed.
   
2. **Configure API Keys**
   The system relies on cloud models. Ensure you have copied `.env.example` to `.env` and populated your Cloud API keys (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).

3. **Run the Bootstrap Script**
   Execute the automated bootstrap script. This will scaffold the `.env` file, initialize the `wiki/` tree structure, and install any local pip dependencies required for CLI tools.
   ```bash
   ./scripts/bootstrap.sh
   ```

4. **Bring up Docker Containers**
   Build and start the entire stack in detached mode:
   ```bash
   docker compose up -d --build
   ```

5. **Verify Health**
   Check `docker ps` or `docker compose logs` to ensure all 6 services are healthy and running without crash loops. Ensure ports 8765 (basic-memory) and 8080 (scout) are bound.
