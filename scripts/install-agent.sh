#!/usr/bin/env bash
# ==============================================================================
# SNP Memory System — Smart Non-Destructive Agent Installer
# ==============================================================================
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/saltless-bruh/memory-system/main/scripts/install-agent.sh | bash
# Or locally:
#   ./scripts/install-agent.sh [target_directory]
# ==============================================================================

set -euo pipefail

TARGET_DIR="${1:-.}"
TARGET_AGENT_DIR="${TARGET_DIR}/.agent"

echo "🧠 Installing SNP Memory System Agent Package..."
echo "📂 Target Directory: $(cd "${TARGET_DIR}" && pwd)"

# Resolve source directory (if run locally from repository)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PACKAGE_SRC="${REPO_ROOT}/packages/snp-agent"

# Temporary directory for remote curl execution if package source not local
TEMP_DIR=""
if [[ ! -d "${PACKAGE_SRC}" ]]; then
    TEMP_DIR="$(mktemp -d)"
    trap 'rm -rf "${TEMP_DIR}"' EXIT
    echo "⬇️  Fetching SNP agent package from GitHub..."
    git clone --depth 1 https://github.com/saltless-bruh/memory-system.git "${TEMP_DIR}/repo" > /dev/null 2>&1
    PACKAGE_SRC="${TEMP_DIR}/repo/packages/snp-agent"
fi

# 1. Create target directory structure
mkdir -p "${TARGET_AGENT_DIR}/rules"
mkdir -p "${TARGET_AGENT_DIR}/instructions"
mkdir -p "${TARGET_AGENT_DIR}/workflows"
mkdir -p "${TARGET_AGENT_DIR}/skills"

# 2. Non-destructively copy rules
cp -f "${PACKAGE_SRC}/rules/snp-memory.md" "${TARGET_AGENT_DIR}/rules/"

# 3. Non-destructively copy instructions
cp -f "${PACKAGE_SRC}/instructions/"*.md "${TARGET_AGENT_DIR}/instructions/"

# 4. Non-destructively copy workflows
cp -f "${PACKAGE_SRC}/workflows/"*.md "${TARGET_AGENT_DIR}/workflows/"

# 5. Non-destructively copy progressive disclosure skills
for skill_dir in "${PACKAGE_SRC}/skills/"snp-*; do
    if [[ -d "${skill_dir}" ]]; then
        skill_name="$(basename "${skill_dir}")"
        mkdir -p "${TARGET_AGENT_DIR}/skills/${skill_name}"
        cp -r "${skill_dir}/"* "${TARGET_AGENT_DIR}/skills/${skill_name}/"
    fi
done

# 6. Scaffold .mcp.json if not present
if [[ ! -f "${TARGET_DIR}/.mcp.json" ]]; then
    cat << 'EOF' > "${TARGET_DIR}/.mcp.json"
{
  "mcpServers": {
    "snp-wiki": {
      "url": "http://localhost:8765/mcp"
    },
    "scout": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
EOF
    echo "📄 Created default .mcp.json"
fi

echo ""
echo "=========================================================================="
echo "✅ SNP Memory System Agent Package Successfully Installed!"
echo "=========================================================================="
echo "  • Rules Installed:        ${TARGET_AGENT_DIR}/rules/snp-memory.md"
echo "  • Workflows Installed:    ${TARGET_AGENT_DIR}/workflows/ (/snp-query, /snp-compile, etc.)"
echo "  • Skills Installed:       ${TARGET_AGENT_DIR}/skills/ (8 progressive disclosure skills)"
echo "  • Instructions Installed: ${TARGET_AGENT_DIR}/instructions/"
echo "=========================================================================="
echo "🚀 NEXT STEP: Open your agent chat (Cursor / Claude / Gemini) and type:"
echo "   > /snp-reload"
echo "=========================================================================="
