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

# The checkout the local stdio server would serve. Set only when this script is
# run from a real clone: a curl install clones into a temp directory that is
# deleted on exit, and pinning a server to a path that will not exist is worse
# than not offering it.
LOCAL_CHECKOUT="${REPO_ROOT}"

# Which revision a remote install fetches.
#
# NOT PINNED, and deliberately not pretending to be. `main` is a moving branch,
# and the documented `curl … | bash` install at the top of this file executes
# whatever it serves today. Pinning to a commit would be worse: every future
# curl install would be frozen on one old revision. The real fix is to tag
# releases and default this to the newest tag — the repository has 0 tags as of
# 2026-08-26, so there is nothing to point at yet. Tracked in
# docs/REMAINING_TASKS.md.
#
# Until then the installer at least makes an install *identifiable*: override
# with SNP_AGENT_REF=<tag|branch|sha>, and the resolved commit is printed.
SNP_AGENT_REF="${SNP_AGENT_REF:-main}"
SNP_AGENT_REPO="${SNP_AGENT_REPO:-https://github.com/saltless-bruh/memory-system.git}"

# Temporary directory for remote curl execution if package source not local
TEMP_DIR=""
if [[ ! -d "${PACKAGE_SRC}" ]]; then
    TEMP_DIR="$(mktemp -d)"
    trap 'rm -rf "${TEMP_DIR}"' EXIT
    echo "⬇️  Fetching SNP agent package from ${SNP_AGENT_REPO} @ ${SNP_AGENT_REF}"
    if ! git clone --depth 1 --branch "${SNP_AGENT_REF}" \
            "${SNP_AGENT_REPO}" "${TEMP_DIR}/repo" > /dev/null 2>&1; then
        echo "❌ Could not clone ${SNP_AGENT_REPO} at ref '${SNP_AGENT_REF}'." >&2
        exit 1
    fi
    INSTALLED_COMMIT="$(git -C "${TEMP_DIR}/repo" rev-parse HEAD)"
    echo "   installed from commit ${INSTALLED_COMMIT}"
    echo "   (ref '${SNP_AGENT_REF}' is not immutable — record this commit)"
    PACKAGE_SRC="${TEMP_DIR}/repo/packages/snp-agent"
    LOCAL_CHECKOUT=""
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
#
# Three servers, matching manifest.json and scripts/export_mcp_config.py:
# snp-wiki reads the vault, scout is the only door into RAG, and snpmemory
# authors and verifies. The third is stdio and needs the checkout it serves
# pinned in argv, because it resolves configuration and relative paths against
# its working directory.
if [[ ! -f "${TARGET_DIR}/.mcp.json" ]]; then
    {
        printf '{\n  "mcpServers": {\n'
        printf '    "snp-wiki": {\n      "url": "http://localhost:8765/mcp"\n    },\n'
        printf '    "scout": {\n      "url": "http://localhost:8080/mcp"\n    }'
        if [[ -n "${LOCAL_CHECKOUT}" ]]; then
            printf ',\n    "snpmemory": {\n      "command": "snpmemory",\n'
            printf '      "args": ["mcp", "--root", "%s"]\n    }' "${LOCAL_CHECKOUT}"
        fi
        printf '\n  }\n}\n'
    } > "${TARGET_DIR}/.mcp.json"
    echo "📄 Created default .mcp.json"
    if [[ -z "${LOCAL_CHECKOUT}" ]]; then
        echo "ℹ️  The local authoring server was left out: this install had no"
        echo "   persistent checkout to point it at. From your memory-system"
        echo "   clone, run: snpmemory mcp-config --client claude"
    fi
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
