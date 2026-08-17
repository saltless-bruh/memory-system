---
name: snp-auto-heal-vault
description: >-
  Use this skill when address verification reports semantic link drift (DRIFT or FAIL errors) or when running automated CI/CD maintenance on Knowledge Vault RAG citations.
---

# snp-auto-heal-vault

## Purpose
The SNP Memory System V2 includes an autonomous Address Auto-Healer ([`scout/healer.py`](scout/healer.py)). When underlying raw documents change or embeddings shift, causing `sources[].hint` addresses to drift or fail verification, the healer queries PostgreSQL 16 `pgvector` hybrid search to re-mint valid hints, patch the wiki Markdown files on a feature branch, and record an audit log in `wiki/log.md`.

## How to use

1. **Verify Address Health First**
   Run the address verifier to detect which addresses have drifted (`DRIFT`) or failed (`FAIL`):
   ```bash
   uv run python scripts/verify_addresses.py
   ```

2. **Execute the Auto-Healer**
   - **Local / Developer Mode** (on a feature/PR branch):
     ```bash
     uv run python scout/healer.py
     ```
   - **CI / Headless Mode** (validates that current branch is not `main`, applies fixes, and logs actions):
     ```bash
     uv run python scout/healer.py --ci
     ```

3. **Inspect the Audit Log**
   Check [`wiki/log.md`](wiki/log.md). The healer records a timestamped audit entry for every auto-healed address:
   ```markdown
   - [2026-08-17 09:49:15 UTC] AUTO-HEAL (DRIFT): paged-attention-engine — hint '...' -> 'PagedAttention Engine' (bot fix, pending review)
   ```

4. **Verify Post-Heal Status**
   Re-run the verification gates to confirm 100% PASS:
   ```bash
   python3 scripts/gen_index.py --check
   uv run python scripts/verify_addresses.py
   ```

5. **Commit and Propose PR**
   Commit the healed `.md` files and open a Pull Request for human review (R-6.4, R-7.3). Never commit directly to `main`.
