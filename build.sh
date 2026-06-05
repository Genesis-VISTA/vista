#!/bin/bash
set -euo pipefail

REPO_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

cd "$REPO_ROOT/mcp-server/mcp-apps"
npm install
npm run build

cd "$REPO_ROOT/mcp-server"
# amscrot-py (the AmSC IRI SDK) is now a base dependency since both NERSC (Perlmutter)
# and OLCF (Frontier) routing rely on it. The git+ssh:// URL requires GitLab SSH keys
# configured for the user running this build — see README.md.
uv sync

# This gets built automatically by the MCP server, but build it here so failures and logs are more clear
cd "$REPO_ROOT/mcp-server/src/vista_mcp_server/docker"
docker build -t vista-sandbox .

cd "$REPO_ROOT/backend"
uv sync

cd "$REPO_ROOT/ui"
npm install

cd "$REPO_ROOT"
