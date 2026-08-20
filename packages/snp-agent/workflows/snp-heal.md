---
description: Autonomously repairs drifted RAG address citations on a PR feature branch via PostgreSQL pgvector.
---

# /snp-heal

Execute the autonomous address healing procedure:

1. **Branch Protection Check**:
   - Verify that the current branch is NOT `main` or `master` (Protected Branch Guard).
   - If on `main`, switch to a feature branch first (`git checkout -b wiki/heal-addresses`).

2. **Execute Closed-Loop Gate**:
   - Run `uv run python scripts/ci_address_gate.py --mode pr`.
   - Exit `2` fails without mutation. Exit `1` permits one scoped heal pass,
     followed by address and lint verification; a failed pass restores the wiki.

3. **Verify Post-Heal Status**:
   - Run `python3 scripts/gen_index.py --check`.
   - Run `uv run python scripts/verify_addresses.py`.
   - Confirm verifier exit `0`. Exit `1` remains semantic drift; exit `2` is an
     infrastructure/configuration failure and must not heal.
   - A residual `FAIL` is **not a healing failure**: it means the addressed file
     returned no chunks for this hint, so there is nothing to re-mint against.
     Stop and escalate it as a content decision (fix or replace the source, drop
     the address, or re-scope the page's department) rather than re-running the
     gate.

4. **Commit Heals**:
   - Commit the healed files and propose changes via Pull Request.
