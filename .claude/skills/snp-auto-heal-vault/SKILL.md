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

   > **Only `DRIFT` is healable.** `DRIFT` means the addressed file is retrievable
   > but the hint lost rank 1 or is not grounded in the file's own text — a better
   > phrase exists and the healer can find it. `FAIL` means the addressed file
   > returned **no chunks at all** (unindexed, empty after parsing, or outside the
   > declaring page's department). There is nothing to re-mint against, so neither
   > the healer nor the closed-loop gate can repair it; it is a **content
   > decision** — fix or replace the source, drop the address, or re-scope the
   > page's department.

2. **Execute the Closed-Loop Gate**
   On a feature/PR branch run:
   ```bash
   uv run python scripts/ci_address_gate.py --mode pr
   ```
   The gate refuses protected/unresolved PR branches, never mutates on verifier
   exit `2`, permits one scoped healer pass on exit `1`, and rolls the wiki back
   unless post-heal address verification and lint both pass.

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
   Review the verified changes and open a Pull Request for human review
   (R-6.4, R-7.3). Scheduled mode creates and pushes a `heal/*` branch from a
   protected base; it does not auto-merge.
