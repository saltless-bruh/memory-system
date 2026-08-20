---
name: snp-bootstrap-system
description: >-
  Use this skill when you need to deploy, restart, or bring up the entire SNP Memory System V2 infrastructure via Docker, PostgreSQL 16, and LiteLLM.
---

# snp-bootstrap-system

## Purpose
The SNP Memory System V2 uses Compose services for Git, LiteLLM, PostgreSQL,
one-shot migrations, Scout, ingestion, host-sync, and basic-memory. This skill
guides ordered bring-up without relying on a stale service count.

## How to use

1. **Verify Host Requirements**
   Ensure Docker, Docker Compose, and Python 3.12+ are installed.
   
2. **Configure API Keys**
   Run `./scripts/bootstrap.sh`, then configure Cloud API, Scout auth, and
   webhook values in `.env`. Keep the generated admin, query, and ingest secret
   files separate; runtime services must not fall back to the admin identity.

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
   - Check container status: `docker compose ps`; `postgres-migrate` must
     complete successfully before Scout and sync-job.
   - Check replica publication: `curl -fsS http://127.0.0.1:9000/ready`.
   - Run `python3 scripts/gen_index.py --check`.
   - Run live `uv run python scripts/verify_addresses.py` only with its backend
     configured; interpret exits as 0 PASS, 1 semantic drift, 2 infrastructure.
