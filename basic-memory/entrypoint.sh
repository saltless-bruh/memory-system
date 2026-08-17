#!/bin/sh
# basic-memory container entry. The config lives in a GLOBAL dir
# (~/.basic-memory) that we persist on a volume for the SQLite DB + vector
# store. config.json/.bmignore are authoritative from the image, so we copy
# them in on every start (the DB in the same dir survives across restarts).
set -e

BM_HOME="${HOME:-/root}/.basic-memory"
mkdir -p "$BM_HOME"
cp /opt/bm/config.json "$BM_HOME/config.json"
cp /opt/bm/.bmignore "$BM_HOME/.bmignore"

# There is no `bm sync` in 0.22 — the initial file->entity scan runs inside
# the MCP server's startup lifespan (setup doc). Bind to 0.0.0.0 so the
# member's IDE (on the host) can reach the search/read tools.
#
# We launch via mcp_launcher.py (not `bm mcp` directly) so the broken generic
# search/fetch tools are dropped — see that file for why. It runs the same
# `bm mcp` command internally (transport/host/port/project baked in there).
exec python /opt/bm/mcp_launcher.py
