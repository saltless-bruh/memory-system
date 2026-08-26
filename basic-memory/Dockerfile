# basic-memory — the LLM-Wiki engine (search/read MCP). AGPL-3.0: the single
# AGPL piece in the stack (runbook §4), fine for internal self-host.
#
# Config is baked from the validated setup (docs/basic-memory-setup.md):
# OFF so the vault is NEVER mutated (R-2.5)
# basic-memory service mounted read-only in Compose. Its configured FastEmbed
# model runs in-process and does not receive the LiteLLM master credential.
# Base pinned by digest 2026-08-26. The tag is kept for readability; the
# digest is the pin. Resolved from the layer the running images were built
# on (python 3.12.13, Debian 13 trixie), not from what the tag means today.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ARG SNP_GIT_REVISION=unknown
LABEL org.opencontainers.image.revision=$SNP_GIT_REVISION
LABEL org.opencontainers.image.source=https://github.com/saltless-bruh/snp-memory-system

# --prefer-binary is REQUIRED: without it pip builds litellm's Rust sdist and
# fails (setup doc).
#
# `basic-memory==0.22.1` was a real pin over a closure of 163 packages that were
# not pinned at all — one line in 164. The lock fixes the whole closure, taken
# from the running container.
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --prefer-binary --require-hashes -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock

COPY config.json /opt/bm/config.json
COPY .bmignore /opt/bm/.bmignore
COPY mcp_launcher.py /opt/bm/mcp_launcher.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8765
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
