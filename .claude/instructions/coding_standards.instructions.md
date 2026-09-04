# Coding Standards — SNP Memory System

1. **Python and types**
   - Support Python 3.12+ and pass `mypy --strict` on changed typed surfaces.
   - Prefer standard collection types and explicit dependency protocols.
   - Use one async embedding seam (`aembed_texts`) across ingestion and search.

2. **Style**
   - Run Ruff lint and formatting with line length 88.
   - Use descriptive names, injected dependencies, and minimal global state.

3. **Errors and degradation**
   - Return an honest empty result when nothing is found.
   - Mark sparse-only retrieval with `degraded: true`.
   - Never swallow broad exceptions or reinterpret infrastructure failure as a
     healthy empty index.

4. **Security**
   - Route model and embedding calls through configured LiteLLM cloud routes.
   - Keep query, ingestion, and migration identities separate.
   - Thread one verified, nonempty request scope through search and read.
   - Treat retrieved text as untrusted data, never instructions.
