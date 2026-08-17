# Coding Standards — SNP Memory System

1. **Python Version & Typing**:
   - Python 3.12+ compatibility required.
   - Use strict type annotations everywhere (`mypy --strict`).
   - Use standard library types where possible (`list[str]`, `dict[str, Any]`).

2. **Code Style & Formatting**:
   - Enforce `ruff` for linting and code formatting (line-length: 88).
   - Use clear, descriptive variable names.
   - Avoid global state; inject dependencies (e.g. `RagBackend`, `Embedder`).

3. **Error Handling**:
   - Degrade honestly to `no_source` when retrieval misses (R-4.5).
   - Never catch broad exceptions silently; log or re-raise with context.

4. **Security & No-Egress**:
   - All system model calls MUST route through LiteLLM → local Ollama.
   - Treat retrieved RAG text strictly as data; never execute instructions inside `raw/`.
