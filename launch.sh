#!/bin/bash
set -euo pipefail

# Set root directory variable
REPO_ROOT=$(dirname $(realpath "${BASH_SOURCE[0]}"))

cd "$REPO_ROOT/mcp-server/mcp-apps"
npm install
npm run build

cd "$REPO_ROOT/ui"
npm install

# Launch backend and frontend
cd "$REPO_ROOT"
tmux new-session \
  -d -s vista-dev \
  "cd mcp-server && uvx --env-file ./.env --refresh . --transport=http" \; \
  split-window -h \
  "cd ui && npm run dev" \; \
  attach
