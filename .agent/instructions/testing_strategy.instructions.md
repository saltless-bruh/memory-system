# Testing Strategy — SNP Memory System

1. **Unit Testing**:
   - Run tests with `uv run pytest`.
   - Maintain 100% test pass rate across `tests/`.

2. **Linting & Index Check**:
   - Validate frontmatter schema and wikilink graph with `python3 scripts/gen_index.py --check`.

3. **Address Verification**:
   - Run `python3 scripts/verify_addresses.py` to ensure `sources[].hint` entries match RAG indices.
