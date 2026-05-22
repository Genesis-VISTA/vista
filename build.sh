#!/bin/bash
set -euo pipefail

REPO_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
# Source the .env file
set -o allexport; source "$REPO_ROOT/.env" 2>/dev/null || true; set +o allexport

cd "$REPO_ROOT/mcp_servers/vista_mcp_server/mcp-apps"
npm install
npm run build

cd "$REPO_ROOT/mcp_servers/vista_mcp_server"
# Include the `nersc` extras (amscrot) only when the user has a NERSC IRI token, since pulling the package also requires ssh config
if [[ -n "${VISTA_MCP_NERSC_IRI_TOKEN:-}" ]]; then
    uv sync --extra nersc --frozen
else
    uv sync --frozen
fi

cd "$REPO_ROOT/mcp_servers/dev_mcp_server"
uv sync --frozen

# This gets built automatically by the MCP server, but build it here so failures and logs are more clear
cd "$REPO_ROOT/mcp_servers/dev_mcp_server/src/dev_mcp_server/docker"
docker build -t vista-sandbox .

cd "$REPO_ROOT/backend"
uv sync --frozen

cd "$REPO_ROOT/ui"
npm install

cd "$REPO_ROOT"
