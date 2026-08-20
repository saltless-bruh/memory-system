# Testing Strategy — SNP Memory System

1. **Unit Testing**:
   - Run deterministic tests with
     `timeout 300s uv run pytest -m 'not integration' --disable-socket -q`.
   - Run live tests separately with
     `uv run pytest -m integration tests/integration --force-enable-socket -q`
     against the disposable integration Compose project.
   - Maintain 100% test pass rate across `tests/`.

2. **Linting & Index Check**:
   - Validate frontmatter schema and wikilink graph with `python3 scripts/gen_index.py --check`.

3. **Address Verification**:
   - Run `uv run python scripts/verify_addresses.py` against live services.
   - Exit `0` is PASS, `1` is semantic FAIL/DRIFT, and `2` is
     infrastructure/configuration failure. Never heal on exit `2`.
