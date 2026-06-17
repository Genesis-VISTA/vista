#!/bin/bash
set -euo pipefail
set -m # set jobcontrol

REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
cd "$REPO_ROOT"

# Args: an optional mode (tmux|terminal|logs) plus an optional --prod flag
MODE="logs"
PROD=''
NO_BUILD=false
for arg in "$@"; do
  case "$arg" in
    --prod) PROD=true ;;
    --no-build) NO_BUILD=true ;; # Skip the build step (e.g. baked into a container image).
    tmux|terminal|logs) MODE="$arg" ;;
    *) echo "Usage: $0 [tmux|terminal|logs] [--prod] [--no-build]" >&2; exit 1 ;;
  esac
done

if [[ "$NO_BUILD" != true ]]; then
  ./scripts/build.sh ${PROD:+--prod}
fi

cd "$REPO_ROOT/backend"
uv run python scripts/seed_db.py

"$REPO_ROOT/scripts/launch_globus.py" --setup

export VISTA_MCP_URL="http://localhost:8000/mcp"
export VISTA_BACKEND_URL="http://localhost:8001"


GLOBUS_CMD="'$REPO_ROOT/scripts/launch_globus.py';"

MCP_CMD="
  cd '$REPO_ROOT/mcp_servers/vista_mcp_server' &&
  uv run vista-mcp-server --transport=http;
"

BACKEND_CMD="
  cd '$REPO_ROOT/backend' &&
  echo 'Waiting for MCP server...' &&
  until curl -s -o /dev/null '$VISTA_MCP_URL'; do sleep 1; done &&
  uv run vista-backend;
"

if [[ "$PROD" == true ]]; then
  UI_RUN_CMD="npm start"
else
  UI_RUN_CMD="npm run dev"
fi
UI_CMD="
  cd '$REPO_ROOT/ui' &&
  echo 'Waiting for backend...' &&
  until curl -s -o /dev/null '$VISTA_BACKEND_URL/openapi.json'; do sleep 1; done &&
  $UI_RUN_CMD;
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
      "$GLOBUS_CMD; exec bash" \; \
      split-window -v \
      "$MCP_CMD; exec bash" \; \
      split-window -h \
      "$BACKEND_CMD; exec bash" \; \
      split-window -v \
      "$UI_CMD; exec bash" \; \
      attach
    ;;
  terminal)
    launch_terminal "Globus Endpoint" "$GLOBUS_CMD; exec bash"
    launch_terminal "Backend" "$BACKEND_CMD; exec bash"
    launch_terminal "UI Dev Server" "$UI_CMD; exec bash"
    launch_terminal "MCP Server" "$MCP_CMD; exec bash"
    ;;
  logs)
    LOG_DIR="$REPO_ROOT/logs"
    rm -rf "$LOG_DIR"
    mkdir -p "$LOG_DIR"

    pids=()
    cleanup() {
      trap - INT TERM EXIT  # Disarm so this only runs once.
      echo "Shutting down..."
      for pid in "${pids[@]}"; do
        kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
      done
      wait "${pids[@]}" 2>/dev/null || true
    }
    trap cleanup INT TERM EXIT

    # Run a service, tee-ing output to a log file and showing a prefix on stdout
    run_service() {
      local name="$1" logfile="$2" cmd="$3"
      ( bash -c "$cmd" 2>&1 | tee "$logfile" | sed -u "s/^/[$name] /" ) &
      pids+=($!)
    }

    echo "All services will be started, logging to:"
    echo "  Globus:     $LOG_DIR/globus.log"
    echo "  MCP server: $LOG_DIR/mcp.log"
    echo "  Backend:    $LOG_DIR/backend.log"
    echo "  UI:         $LOG_DIR/ui.log"
    echo "Press Ctrl-C to stop all services."

    run_service globus "$LOG_DIR/globus.log" "$GLOBUS_CMD"
    run_service backend "$LOG_DIR/backend.log" "$BACKEND_CMD"
    run_service ui "$LOG_DIR/ui.log" "$UI_CMD"
    run_service mcp "$LOG_DIR/mcp.log" "$MCP_CMD"

    wait
    ;;
  *)
    echo "Usage: $0 [tmux|terminal|logs]"
    exit 1
    ;;
esac
