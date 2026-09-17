#!/bin/bash
set -euo pipefail
set -m # set jobcontrol

REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
cd "$REPO_ROOT"

# Source the .env file if there is one, matching build.sh. This script did not
# read it before, which the Globus gate below needs it to: the refresh tokens
# that decide whether transfer setup is worth attempting live there.
#
# Guarded with a file test rather than `source ... || true`: bash treats a
# missing *script* file as fatal and exits before the `||` is considered, so
# the tolerant-looking form kills the script on a checkout with no .env.
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -o allexport; source "$REPO_ROOT/.env"; set +o allexport
fi

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

# Globus Transfer moves files for Odo and Frontier. Perlmutter never touches
# it -- every file operation there goes through the NERSC IRI filesystem API --
# so a researcher using only Perlmutter needs none of this.
#
# Gated *and* non-fatal, because those are two different failures. Gated
# because the endpoint authenticates with a refresh token, so without one
# setup is a one-time browser login asked of a researcher who has nothing to
# use it for. Non-fatal because satisfying the gate is not the same as being
# able to finish: no browser to log in with, no network, or the login is
# declined. In every one of those cases the rest of VISTA is still perfectly
# usable.
#
# `scripts/package_launcher.sh` gates the packaged artifact the same way, on
# the same two variables, and calls the same entry point.
GLOBUS_READY=false
if [[ -n "${VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN:-}" \
   || -n "${VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN:-}" ]]; then
  if "$REPO_ROOT/scripts/launch_globus.py" --setup; then
    GLOBUS_READY=true
  else
    echo "warning: Globus endpoint setup did not complete." >&2
    echo "warning:   File operations for Odo and Frontier are unavailable;" >&2
    echo "warning:   job submission to those clusters will report the" >&2
    echo "warning:   incomplete setup. Perlmutter is unaffected." >&2
  fi
fi

export VISTA_MCP_URL="http://localhost:8000/mcp"
export VISTA_BACKEND_URL="http://localhost:8001"


# `--start`, not a bare call: setup already ran above, on this terminal, and
# this process is the held endpoint. In `logs` mode it has no tty, so a call
# that still tried to set up would refuse for want of one it was never supposed
# to need. `scripts/package_launcher.sh` starts it the same way.
GLOBUS_CMD="'$REPO_ROOT/scripts/launch_globus.py' --start;"

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
    # Built up pane by pane rather than as one chained command so the Globus
    # pane can be omitted; the endpoint process is pointless without setup.
    tmux new-session -d -s vista-dev "$MCP_CMD; exec bash"
    tmux split-window -v "$BACKEND_CMD; exec bash"
    tmux split-window -h "$UI_CMD; exec bash"
    if [[ "$GLOBUS_READY" == true ]]; then
      tmux split-window -v "$GLOBUS_CMD; exec bash"
    fi
    tmux attach -t vista-dev
    ;;
  terminal)
    if [[ "$GLOBUS_READY" == true ]]; then
      launch_terminal "Globus Endpoint" "$GLOBUS_CMD; exec bash"
    fi
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
    if [[ "$GLOBUS_READY" == true ]]; then
      echo "  Globus:     $LOG_DIR/globus.log"
    fi
    echo "  MCP server: $LOG_DIR/mcp.log"
    echo "  Backend:    $LOG_DIR/backend.log"
    echo "  UI:         $LOG_DIR/ui.log"
    echo "Press Ctrl-C to stop all services."

    if [[ "$GLOBUS_READY" == true ]]; then
      run_service globus "$LOG_DIR/globus.log" "$GLOBUS_CMD"
    fi
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
