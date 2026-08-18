---
description: Autonomously repairs drifted RAG address citations on a PR feature branch via PostgreSQL pgvector.
---

# /snp-heal

Execute the autonomous address healing procedure:

1. **Branch Protection Check**:
   - Verify that the current branch is NOT `main` or `master` (Protected Branch Guard).
   - If on `main`, switch to a feature branch first (`git checkout -b wiki/heal-addresses`).

2. **Execute Auto-Healer**:
   - **Local Mode**: Run `uv run python scout/healer.py`.
   - **CI Mode**: Run `uv run python scout/healer.py --ci`.
   - The healer queries PostgreSQL pgvector, re-mints drifted hints, patches the `.md` files, and appends an audit record to `wiki/log.md`.

3. **Verify Post-Heal Status**:
   - Run `python3 scripts/gen_index.py --check`.
   - Run `uv run python scripts/verify_addresses.py`.
   - Confirm that all addresses now report `PASS`.

4. **Commit Heals**:
   - Commit the healed files and propose changes via Pull Request.
