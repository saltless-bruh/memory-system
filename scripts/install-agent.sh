#!/usr/bin/env bash
# ==============================================================================
# SNP Memory System — Smart Non-Destructive Agent Installer
# ==============================================================================
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/saltless-bruh/memory-system/main/scripts/install-agent.sh | bash
# Or locally:
#   ./scripts/install-agent.sh [target_directory] [--client portable|opencode]
#                             [--dry-run] [--confirm]
# ==============================================================================

set -euo pipefail

TARGET_DIR="."
TARGET_SET=false
CLIENT="portable"
DRY_RUN=false
CONFIRM=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --client)
            if [[ $# -lt 2 ]]; then
                echo "--client requires portable or opencode" >&2
                exit 3
            fi
            CLIENT="$2"
            shift 2
            ;;
        --client=*) CLIENT="${1#*=}"; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --confirm) CONFIRM=true; shift ;;
        --help|-h)
            echo "Usage: install-agent.sh [directory] [--client portable|opencode] [--dry-run] [--confirm]"
            exit 0
            ;;
        --)
            shift
            if [[ $# -ne 1 ]] || [[ "${TARGET_SET}" == true ]]; then
                echo "Expected one target directory" >&2
                exit 3
            fi
            TARGET_DIR="$1"
            TARGET_SET=true
            shift
            ;;
        -*) echo "Unknown option: $1" >&2; exit 3 ;;
        *)
            if [[ "${TARGET_SET}" == true ]]; then
                echo "Expected one target directory" >&2
                exit 3
            fi
            TARGET_DIR="$1"
            TARGET_SET=true
            shift
            ;;
    esac
done
if [[ "${CLIENT}" != portable && "${CLIENT}" != opencode ]]; then
    echo "Unknown client: ${CLIENT}; expected portable or opencode" >&2
    exit 3
fi
if [[ ! -d "${TARGET_DIR}" ]]; then
    echo "Target must be an existing directory: ${TARGET_DIR}" >&2
    exit 3
fi
TARGET_DIR="$(cd -- "${TARGET_DIR}" && pwd)"
TARGET_AGENT_DIR="${TARGET_DIR}/.agent"

echo "🧠 Installing SNP Memory System Agent Package..."
echo "📂 Target Directory: $(cd "${TARGET_DIR}" && pwd)"

# Resolve source directory (if run locally from repository)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PACKAGE_SRC="${REPO_ROOT}/packages/snp-agent"

# A preview must not clone, create temporary directories, or scaffold a target.
# A local OpenCode preview uses the same complete validator as installation.
if [[ "${DRY_RUN}" == true ]] && { [[ "${CLIENT}" == portable ]] || [[ ! -d "${PACKAGE_SRC}" ]]; }; then
    echo "[dry-run] ${CLIENT} installation would write:"
    if [[ "${CLIENT}" == opencode ]]; then
        echo "  ${TARGET_DIR}/.opencode/skills/snp-*"
        echo "  ${TARGET_DIR}/.opencode/snp/{rules,instructions,workflows}"
        echo "  ${TARGET_DIR}/opencode.json"
        echo "Package source is remote; no package was fetched for this preview."
    else
        for component in rules instructions workflows skills; do
            echo "  ${TARGET_AGENT_DIR}/${component}"
        done
        if [[ ! -f "${TARGET_DIR}/.mcp.json" ]]; then
            echo "  ${TARGET_DIR}/.mcp.json"
        fi
    fi
    exit 0
fi

# Whether this script is running from a real clone rather than a temp directory
# a curl install deletes on exit. Nothing in the scaffolded config depends on it
# any more — the one server it writes is a URL — but the closing advice does:
# `snpmemory mcp-config` has to be run from a checkout.
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

if [[ "${CLIENT}" == opencode ]]; then
    PACKAGE_REPO="$(cd "${PACKAGE_SRC}/../.." && pwd)"
    if [[ -n "${SNP_AGENT_PYTHON:-}" ]]; then
        INSTALLER_PYTHON="${SNP_AGENT_PYTHON}"
    elif [[ -x "${PACKAGE_REPO}/.venv/bin/python" ]]; then
        INSTALLER_PYTHON="${PACKAGE_REPO}/.venv/bin/python"
    else
        INSTALLER_PYTHON="python3"
    fi
    OPENCODE_ARGS=("${TARGET_DIR}" --package "${PACKAGE_SRC}")
    if [[ "${DRY_RUN}" == true ]]; then OPENCODE_ARGS+=(--dry-run); fi
    if [[ "${CONFIRM}" == true ]]; then OPENCODE_ARGS+=(--confirm); fi
    # -B keeps even a source-checkout preview free of Python bytecode writes.
    "${INSTALLER_PYTHON}" -B "${PACKAGE_REPO}/scripts/install_opencode_agent.py" "${OPENCODE_ARGS[@]}"
    exit 0
fi

# 1. Create target directory structure
mkdir -p "${TARGET_AGENT_DIR}/rules"
mkdir -p "${TARGET_AGENT_DIR}/instructions"
mkdir -p "${TARGET_AGENT_DIR}/workflows"
mkdir -p "${TARGET_AGENT_DIR}/skills"

# Remove only the SNP-owned components withdrawn or renamed by V3. This is
# bounded cleanup, not a mirror delete: custom rules, skills, and workflows
# survive. The two renamed retrieval skills are listed because a rename is
# additive over an existing tree -- without this, an upgrade leaves the old
# directory beside its replacement and the agent sees four retrieval skills,
# two of them naming a retired tool.
rm -rf "${TARGET_AGENT_DIR}/skills/snp-auto-heal-vault"
rm -rf "${TARGET_AGENT_DIR}/skills/snp-rag-fetch"
rm -rf "${TARGET_AGENT_DIR}/skills/snp-search-wiki"
rm -f "${TARGET_AGENT_DIR}/workflows/snp-heal.md"

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
# One server, matching plugin.json, packages/snp-agent/mcp.json and
# scripts/export_mcp_config.py: the authenticated Scout connection. The local
# `snpmemory` stdio server that used to be written beside it was deleted in
# leaf-4.3, so there is no second entry and no checkout to pin in argv.
if [[ ! -f "${TARGET_DIR}/.mcp.json" ]]; then
    {
        printf '{\n  "mcpServers": {\n'
        printf '    "scout": {\n      "url": "http://localhost:8080/mcp",\n'
        printf '      "headers": {"Authorization": "${SCOUT_AUTH_HEADER}"}\n    }'
        printf '\n  }\n}\n'
    } > "${TARGET_DIR}/.mcp.json"
    echo "📄 Created default .mcp.json"
    if [[ -z "${LOCAL_CHECKOUT}" ]]; then
        echo "ℹ️  From your memory-system clone, run"
        echo "   snpmemory mcp-config --client claude to regenerate this file."
    fi
else
    echo "ℹ️  Preserved existing .mcp.json; run snpmemory mcp-config --client claude"
    echo "   to reconcile SNP-managed server entries without replacing custom ones."
fi

echo ""
echo "=========================================================================="
echo "✅ SNP Memory System Agent Package Successfully Installed!"
echo "=========================================================================="
echo "  • Rules Installed:        ${TARGET_AGENT_DIR}/rules/snp-memory.md"
echo "  • Workflows Installed:    ${TARGET_AGENT_DIR}/workflows/ (/snp-query, /snp-compile, etc.)"
echo "  • Skills Installed:       ${TARGET_AGENT_DIR}/skills/ (7 progressive disclosure skills)"
echo "  • Instructions Installed: ${TARGET_AGENT_DIR}/instructions/"
echo "=========================================================================="
echo "🚀 NEXT STEP: Open your agent chat (Cursor / Claude / Gemini) and type:"
echo "   > /snp-reload"
echo "=========================================================================="
