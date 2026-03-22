#!/bin/bash
set -euo pipefail

# Set root directory variable
REPO_ROOT=$(dirname $(realpath "${BASH_SOURCE[0]}"))

MODE="${1:-tmux}"

cd "$REPO_ROOT/mcp-server/mcp-apps"
npm install
npm run build

cd "$REPO_ROOT/mcp-server"
# Pre-populate the uvx cache
uvx --env-file ../.env --refresh . --version

cd "$REPO_ROOT/ui"
npm install

cd "$REPO_ROOT"
UI_CMD="cd '$REPO_ROOT/ui' && npm run dev; exec bash"
SERVER_CMD="cd '$REPO_ROOT/mcp-server' && uvx --env-file ../.env . --transport=http; exec bash"

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
      "$UI_CMD" \; \
      split-window -h \
      "$SERVER_CMD" \; \
      attach
    ;;
  terminal)
    launch_terminal "UI Dev Server" "$UI_CMD"
    launch_terminal "MCP Server" "$SERVER_CMD"
    ;;
  *)
    echo "Usage: $0 [tmux|terminal]"
    exit 1
    ;;
esac
