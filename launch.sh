#!/bin/bash
set -euo pipefail

REPO_ROOT=$(dirname $(realpath "${BASH_SOURCE[0]}"))
cd "$REPO_ROOT"

cd "$REPO_ROOT/mcp-server/mcp-apps"
npm install
npm run build

cd "$REPO_ROOT/backend"
uv venv --python=3.12 --allow-existing .venv
source .venv/bin/activate
uv pip install -e .[dev]

cd "$REPO_ROOT/frontend"
npm install
npm run build

cd "$REPO_ROOT"
tmux new-session \
  -d -s vista-dev \
  "cd '$REPO_ROOT/backend' && source .venv/bin/activate && python -m vista_backend.app" \; \
  split-window -h \
  "cd '$REPO_ROOT/frontend' && npm run dev" \; \
  attach
