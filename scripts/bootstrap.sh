#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "SNP Memory System — Environment Bootstrap"
echo "=================================================="

# Local secret files, created without overwriting existing values.
echo "[1/5] Ensuring local secret files..."
python3 scripts/bootstrap_secrets.py

# .env, copied from the example only when absent.
if [ ! -f .env ]; then
    echo "[2/5] Creating .env from .env.example..."
    cp .env.example .env
else
    echo "[2/5] .env file exists."
fi

echo "[3/5] Initializing Wiki Vault tree structure..."
mkdir -p wiki/playbooks wiki/concepts wiki/techniques wiki/entities
touch wiki/playbooks/.gitkeep wiki/concepts/.gitkeep wiki/techniques/.gitkeep wiki/entities/.gitkeep

# Python dependencies go into ./.venv, which the README activates next. Both
# branches create it: `uv pip install` refuses to run without an environment
# (it never falls back to the system interpreter), and a bare `pip install`
# would write into whatever interpreter happens to be first on PATH.
if command -v uv &>/dev/null; then
    echo "[4/5] Installing Python dependencies into .venv with uv..."
    uv sync --frozen --extra dev
else
    echo "[4/5] Installing Python dependencies into .venv with pip..."
    python3 -m venv .venv
    .venv/bin/python -m pip install -e ".[dev]"
fi

echo "[5/5] Running Wiki Vault Linter..."
.venv/bin/python scripts/gen_index.py --check

echo "=================================================="
echo "Bootstrap complete. Next:"
echo "  source .venv/bin/activate"
echo "  SNP_GIT_REVISION=\$(git rev-parse HEAD) docker compose up -d --build"
echo "=================================================="
