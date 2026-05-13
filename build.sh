#!/bin/bash
set -euo pipefail

REPO_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

cd "$REPO_ROOT/mcp-server/mcp-apps"
npm install
npm run build

cd "$REPO_ROOT/mcp-server"
# Include the `nersc` extras (amscrot) only when the user has a NERSC IRI token
# configured in .env. Plain `uv sync` would otherwise uninstall amscrot on every build.
SYNC_EXTRAS=()
if grep -qE '^[[:space:]]*VISTA_MCP_NERSC_IRI_TOKEN[[:space:]]*=' "$REPO_ROOT/.env" 2>/dev/null; then
    SYNC_EXTRAS+=(--extra nersc)
fi
uv sync "${SYNC_EXTRAS[@]}"

# This gets built automatically by the MCP server, but build it here so failures and logs are more clear
cd "$REPO_ROOT/mcp-server/src/vista_mcp_server/docker"
docker build -t vista-sandbox .

cd "$REPO_ROOT/ui"
npm install

cd "$REPO_ROOT"
