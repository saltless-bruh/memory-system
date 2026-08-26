# Scout image — one build for runtime and one-shot database setup.
#   * `scout`    → python -m scout.serve     (agent-facing MCP; the ONLY
#                  door into rag — R-4.1/R-4.2)
#   * `sync-job` → python -m scout.sync_job  (raw/ auto-ingest — Nhịp A,
#                  T-3.2/R-6.1)
# The library itself is stdlib-only; requirements are just the two service
# entry points' deps (fastmcp, watchfiles).
# Base pinned by digest 2026-08-26. The tag is kept for readability; the
# digest is the pin. Resolved from the layer the running images were built
# on (python 3.12.13, Debian 13 trixie), not from what the tag means today.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

# Installs from the hash-locked file, never from requirements.txt. The ranges
# in requirements.txt are the human input to regenerating this lock; nothing
# builds from them. Measured 2026-08-26: `pypdf>=4.0.0` had silently selected
# 6.16.1 — two major versions above its floor, in the library that parses every
# document in the corpus.
COPY scout/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.lock

# The scout package lands at /app/scout so `import scout.*` resolves.
COPY scout /app/scout/
COPY scripts /app/scripts/
COPY config/postgres/migrations /app/config/postgres/migrations/

# Stamp what this image was built from. Without it a stale image is
# indistinguishable from a fresh one, and the failure it causes is read as a
# live bug: on 2026-08-24 an image five hours older than a batch-splitting fix
# produced a provider 400 for a cap the repository already respected.
# `scripts/preflight_stack.py` compares this against `git rev-parse HEAD`.
ARG SNP_GIT_REVISION=unknown
LABEL org.opencontainers.image.revision=$SNP_GIT_REVISION
LABEL org.opencontainers.image.source=https://github.com/saltless-bruh/snp-memory-system

# Default = the agent-facing MCP server; sync-job overrides `command:`.
EXPOSE 8080
CMD ["python", "-m", "scout.serve"]
