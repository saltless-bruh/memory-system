# basic-memory — the LLM-Wiki engine (search/read MCP). AGPL-3.0: the single
# AGPL piece in the stack (runbook §4), fine for internal self-host.
#
# Config is baked from the validated setup (docs/basic-memory-setup.md):
# OFF so the vault is NEVER mutated (R-2.5)
# basic-memory service
# bind-mounted read-only in compose. Embeddings hit the LiteLLM proxy
# which routes to Cloud APIs.
FROM python:3.12-slim

# --prefer-binary is REQUIRED: without it pip builds litellm's Rust sdist and
# fails (setup doc). Pinned to the Gate-validated version.
RUN pip install --no-cache-dir --prefer-binary "basic-memory==0.22.1"

COPY config.json /opt/bm/config.json
COPY .bmignore /opt/bm/.bmignore
COPY mcp_launcher.py /opt/bm/mcp_launcher.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8765
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
