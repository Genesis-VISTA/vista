#!/bin/bash
set -euo pipefail

REPO_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
cd "$REPO_ROOT"

MODE="${1:-terminal}"

./build.sh

export VISTA_MCP_URL="http://localhost:8000/mcp"
export VISTA_BACKEND_URL="http://localhost:8001"

MCP_CMD="
  cd '$REPO_ROOT/mcp-server' &&
  uv run vista-mcp-server --transport=http;
  exec bash
"

BACKEND_CMD="
  cd '$REPO_ROOT/backend' &&
  echo 'Waiting for MCP server...' &&
  until curl -s -o /dev/null '$VISTA_MCP_URL'; do sleep 1; done &&
  uv run vista-backend;
  exec bash
"

UI_CMD="
  cd '$REPO_ROOT/ui' &&
  echo 'Waiting for backend...' &&
  until curl -fs -o /dev/null '$VISTA_BACKEND_URL/openapi.json'; do sleep 1; done &&
  npm run dev;
  exec bash
"

# Launches a command in a new terminal window
launch_terminal() {
  local title="$1" cmd="$2"
  case "$(uname -s)" in
    Linux)
      if command -v gnome-terminal &>/dev/null; then
        gnome-terminal --title="$title" -- bash -c "$cmd"
      elif command -v konsole &>/dev/null; then
        konsole --new-tab -e bash -c "$cmd" &
      elif command -v xterm &>/dev/null; then
        xterm -title "$title" -e bash -c "$cmd" &
      else
        echo "No supported terminal emulator found (tried gnome-terminal, konsole, xterm)."
        exit 1
      fi
      ;;
    Darwin)
      osascript -e "tell application \"Terminal\" to do script \"$cmd\""
      ;;
    MINGW*|MSYS*|CYGWIN*)
      start cmd /c "$cmd"
      ;;
    *)
      echo "Unsupported platform: $(uname -s)"
      exit 1
      ;;
  esac
}

case "$MODE" in
  tmux)
    tmux new-session \
      -d -s vista-dev \
      "$MCP_CMD" \; \
      split-window -h \
      "$BACKEND_CMD" \; \
      split-window -v \
      "$UI_CMD" \; \
      attach
    ;;
  terminal)
    launch_terminal "Backend" "$BACKEND_CMD"
    launch_terminal "UI Dev Server" "$UI_CMD"
    launch_terminal "MCP Server" "$MCP_CMD"
    ;;
  *)
    echo "Usage: $0 [tmux|terminal]"
    exit 1
    ;;
esac
