#!/usr/bin/env bash
# ==============================================================================
# MyAgent MCP Inspector Safe Launcher (Ports 18674 & 18675)
#
# Dedicated ports:
#   - MCP Inspector Web UI: 18674 (prevents collision with Vite dev server 5173)
#   - MCP Sandbox Proxy:    18675
#
# Core Security Invariants:
#   1. Non-destructive: Never terminates running services; checks ports first.
#   2. Production Safety: Rejects execution if database resolves, symlinks, or
#      hardlinks to production data/my_agent.db or external repositories.
#   3. Isolation: Fresh, private session directory with mode 0700 per run.
#   4. Data Integrity: SQLite Backup API snapshot with PRAGMA integrity_check.
#   5. No Overwrite: Strictly prohibits overwriting or deleting existing files.
#   6. Secret Redaction: Generates fresh cryptographic tokens per session;
#      tokens are written to mode 0600 token files and redacted from logs.
# ==============================================================================

set -euo pipefail

REPO_ROOT="/Users/haohg/Project/my-agent"
VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
INSPECTOR_PORT="${INSPECTOR_PORT:-18674}"
SANDBOX_PORT="${SANDBOX_PORT:-18675}"

echo "================================================================================"
echo "MyAgent MCP Inspector Safe Launcher"
echo "Target Workspace: ${REPO_ROOT}"
echo "Dedicated Ports:  Inspector=${INSPECTOR_PORT}, Sandbox=${SANDBOX_PORT}"
echo "================================================================================"

# 1. Pre-flight Port and Security Preparation via core.inspector_db_safety
CUSTOM_DB_ARG=()
if [ -n "${MYAGENT_DB_PATH:-}" ]; then
  CUSTOM_DB_ARG=("--custom-db" "${MYAGENT_DB_PATH}")
fi

PREP_OUTPUT=$(
  "${VENV_PYTHON}" -m core.inspector_db_safety prepare \
    --repo-root "${REPO_ROOT}" \
    --inspector-port "${INSPECTOR_PORT}" \
    --sandbox-port "${SANDBOX_PORT}" \
    "${CUSTOM_DB_ARG[@]}"
) || {
  EXIT_CODE=$?
  echo "[ABORT] Safety pre-flight check failed with code ${EXIT_CODE}."
  exit "${EXIT_CODE}"
}

# Parse JSON safely with Python
SESSION_DIR=$("${VENV_PYTHON}" -c "import json, sys; print(json.loads(sys.argv[1]).get('session_dir') or '')" "${PREP_OUTPUT}")
RESOLVED_DB_PATH=$("${VENV_PYTHON}" -c "import json, sys; print(json.loads(sys.argv[1])['db_path'])" "${PREP_OUTPUT}")
SESSION_TOKEN=$("${VENV_PYTHON}" -c "import json, sys; print(json.loads(sys.argv[1])['token'])" "${PREP_OUTPUT}")
MASKED_TOKEN=$("${VENV_PYTHON}" -c "import json, sys; print(json.loads(sys.argv[1])['token_masked'])" "${PREP_OUTPUT}")
TOKEN_FILE=$("${VENV_PYTHON}" -c "import json, sys; print(json.loads(sys.argv[1]).get('token_file') or '')" "${PREP_OUTPUT}")
SESSION_MODE=$("${VENV_PYTHON}" -c "import json, sys; print(json.loads(sys.argv[1])['mode'])" "${PREP_OUTPUT}")

# Safe cleanup trap on exit if ephemeral session was created
cleanup_on_exit() {
  if [ -n "${SESSION_DIR}" ] && [ -d "${SESSION_DIR}" ]; then
    echo ""
    echo "[CLEANUP] Cleaning up ephemeral session directory..."
    "${VENV_PYTHON}" -m core.inspector_db_safety cleanup --session-dir "${SESSION_DIR}" || true
  fi
}
trap cleanup_on_exit EXIT INT TERM

echo "[OK] Environment pre-flight validated."
echo "     Session Mode:     ${SESSION_MODE}"
echo "     Active DB Target: ${RESOLVED_DB_PATH}"
if [ -n "${TOKEN_FILE}" ]; then
  echo "     Token File:       ${TOKEN_FILE} (mode 0600)"
fi
echo "     Session Token:    ${MASKED_TOKEN}"
echo "--------------------------------------------------------------------------------"

# 2. Environment Variable Configuration
export CLIENT_PORT="${INSPECTOR_PORT}"
export MCP_SANDBOX_PORT="${SANDBOX_PORT}"
export SERVER_PORT="${SANDBOX_PORT}"
export MYAGENT_WORKSPACE="${REPO_ROOT}"
export MYAGENT_DB_PATH="${RESOLVED_DB_PATH}"
export MCP_INSPECTOR_API_TOKEN="${SESSION_TOKEN}"

echo "Starting MCP Inspector..."
echo "Web UI URL:  http://127.0.0.1:${INSPECTOR_PORT}"
echo "Sandbox URL: http://127.0.0.1:${SANDBOX_PORT}/sandbox"
echo "Authentication Token is protected and loaded securely."
echo "Press Ctrl+C to terminate session."
echo "--------------------------------------------------------------------------------"

exec npx -y @modelcontextprotocol/inspector \
  --cwd "${REPO_ROOT}" \
  "${VENV_PYTHON}" -m my_agent_mcp.server
