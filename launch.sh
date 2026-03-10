#!/bin/bash
set -euo pipefail

# Set root directory variable
REPO_ROOT=$(dirname $(realpath "${BASH_SOURCE[0]}"))

cd "$REPO_ROOT/mcp-server/mcp-apps"
npm install
npm run build

cd "$REPO_ROOT/mcp-server"
# Pre-populate the uvx cache
uvx --env-file ./.env --refresh . --version

cd "$REPO_ROOT/ui"
npm install

read -p 'HPC Username: ' VISTA_MCP_HPC_USERNAME
read -p 'HPC Password: ' -s VISTA_MCP_HPC_PASSWORD
export VISTA_MCP_HPC_USERNAME
export VISTA_MCP_HPC_PASSWORD

# Launch backend and frontend
tmux new-session \
  -d -s vista-dev \
  "cd mcp-server && uvx --env-file ./.env . --transport=http" \; \
  split-window -h \
  "cd ui && npm run dev" \; \
  attach