#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "SNP Memory System — Environment Bootstrap"
echo "=================================================="

# 1. Ensure local secret files exist without overwriting existing values
python3 scripts/bootstrap_secrets.py

# 2. Ensure .env exists
if [ ! -f .env ]; then
    echo "[1/4] Creating .env from .env.example..."
    cp .env.example .env
else
    echo "[1/4] .env file exists."
fi

# 3. Ensure wiki vault tree directories exist
echo "[2/4] Initializing Wiki Vault tree structure..."
mkdir -p wiki/playbooks wiki/concepts wiki/techniques wiki/entities
touch wiki/playbooks/.gitkeep wiki/concepts/.gitkeep wiki/techniques/.gitkeep wiki/entities/.gitkeep

# 4. Ensure python dependencies are installed
if command -v uv &>/dev/null; then
    echo "[3/4] Installing Python dependencies with uv..."
    uv pip install -e . -e ".[dev]"
else
    echo "[3/4] Installing Python dependencies with pip..."
    pip install -e . -e ".[dev]"
fi

# 5. Verify Wiki Vault Lint
echo "[4/4] Running Wiki Vault Linter..."
python3 scripts/gen_index.py --check

echo "=================================================="
echo "Bootstrap complete! You can now bring up services:"
echo "  docker compose up -d --build"
echo "=================================================="
