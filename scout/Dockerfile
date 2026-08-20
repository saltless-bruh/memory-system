# Scout image — one build for runtime and one-shot database setup.
#   * `scout`    → python -m scout.serve     (agent-facing MCP; the ONLY
#                  door into rag — R-4.1/R-4.2)
#   * `sync-job` → python -m scout.sync_job  (raw/ auto-ingest — Nhịp A,
#                  T-3.2/R-6.1)
# The library itself is stdlib-only; requirements are just the two service
# entry points' deps (fastmcp, watchfiles).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY scout/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The scout package lands at /app/scout so `import scout.*` resolves.
COPY scout /app/scout/
COPY scripts /app/scripts/
COPY config/postgres/migrations /app/config/postgres/migrations/

# Default = the agent-facing MCP server; sync-job overrides `command:`.
EXPOSE 8080
CMD ["python", "-m", "scout.serve"]
