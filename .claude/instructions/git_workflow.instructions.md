# Git Workflow — SNP Memory System

1. **PR-First Changes**:
   - Never commit directly to `main`.
   - Create a feature or note branch, commit changes, and propose a PR.

2. **Automated Verification**:
   - Before opening a PR, ensure `python3 scripts/gen_index.py --check` passes.
   - Run `timeout 300s uv run pytest -m 'not integration' --disable-socket -q`.
   - Run live integration tests separately only against the disposable
     `snp-memory-it` project.

3. **Index Generation**:
   - Do NOT hand-edit `wiki/index.md`. Run `python3 scripts/gen_index.py` to regenerate upon note changes.
