#!/bin/bash
set -euo pipefail
set -m # set jobcontrol

REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
cd "$REPO_ROOT"

# Source the .env file if there is one, matching build.sh, so a checkout runs
# with the same configuration the services would read for themselves.
#
# Guarded with a file test rather than `source ... || true`: bash treats a
# missing *script* file as fatal and exits before the `||` is considered, so
# the tolerant-looking form kills the script on a checkout with no .env.
if [[ -f "$REPO_ROOT/.env" ]]; then
  # Strip \r so a .env saved with CRLF endings (Windows) still parses. Read
  # through eval rather than `source <(...)`: macOS ships bash 3.2, where
  # sourcing a process substitution silently reads nothing.
  set -o allexport; eval "$(tr -d '\r' < "$REPO_ROOT/.env")"; set +o allexport
fi

# Args: an optional mode (tmux|terminal|logs) plus optional flags
MODE="logs"
PROD=''
NO_BUILD=false
ELECTRON=''
NO_ELECTRON=false
for arg in "$@"; do
  case "$arg" in
    --prod) PROD=true ;;
    --no-build) NO_BUILD=true ;; # Skip the build step (e.g. baked into a container image).
    --electron) ELECTRON=true ;; # The default in logs mode; kept so existing invocations still work.
    --no-electron) NO_ELECTRON=true ;; # Services only; open http://localhost:3000 in a browser.
    tmux|terminal|logs) MODE="$arg" ;;
    *) echo "Usage: $0 [tmux|terminal|logs] [--prod] [--no-build] [--no-electron]" >&2; exit 1 ;;
  esac
done

if [[ -n "$ELECTRON" && "$NO_ELECTRON" == true ]]; then
  echo "--electron and --no-electron contradict each other; pass one." >&2
  exit 1
fi

# The window's lifetime is the stack's, which only `logs` mode owns: tmux and
# terminal hand the services to other windows and return.
if [[ -n "$ELECTRON" && "$MODE" != logs ]]; then
  echo "--electron works in logs mode only; $MODE mode does not own the services' lifetime." >&2
  exit 1
fi

# So in logs mode the UI opens in the VISTA window unless asked not to, and
# closing the window stops the stack.
if [[ "$MODE" == logs && "$NO_ELECTRON" != true ]]; then
  ELECTRON=true
fi

# Fail before building rather than after: without a display the window cannot
# open, and a session with no window has to say so rather than fall back.
if [[ -n "$ELECTRON" && "$(uname -s)" == Linux && -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  echo "No display (DISPLAY and WAYLAND_DISPLAY are unset), so the VISTA window cannot open." >&2
  echo "Pass --no-electron to start the services alone and use a browser." >&2
  exit 1
fi

if [[ "$NO_BUILD" != true ]]; then
  ./scripts/build.sh ${PROD:+--prod} ${ELECTRON:+--electron}
fi

# path.txt is what Electron's installer (`install-electron`, which build.sh
# runs after `npm ci`; Electron 44 has no postinstall) writes once it has the
# binary. The .bin/electron link exists without it, after any bare `npm ci`,
# and then fails to start with a confusing message.
if [[ -n "$ELECTRON" && ! -f "$REPO_ROOT/electron/node_modules/electron/path.txt" ]]; then
  echo "The VISTA window is not installed; run ./scripts/build.sh --electron first, or pass --no-electron." >&2
  exit 1
fi

cd "$REPO_ROOT/backend"
uv run python scripts/seed_db.py

export VISTA_MCP_URL="http://localhost:8000/mcp"
export VISTA_BACKEND_URL="http://localhost:8001"


# No Globus service here, and nothing to start: OLCF file operations are HTTPS
# requests against the cluster's own collection, so VISTA no longer runs a
# Globus endpoint of its own. The credential is the researcher's, connected in
# the interface -- long after this script has finished.

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

# localhost, not 127.0.0.1: that is where `next dev` answers. --dev keeps
# DevTools and force-reload in the window's menu.
UI_URL="http://localhost:3000"
# On Linux, the same sandbox decision the package makes (linux-desktop-window
# D8): window-sandbox prints nothing or --no-sandbox, and says why on stderr,
# which shows with the window's other output. Left for the window's own shell
# to run, so the single quotes keep it unexpanded here.
WINDOW_SANDBOX=''
if [[ "$(uname -s)" == Linux ]]; then
  # shellcheck disable=SC2016
  WINDOW_SANDBOX='$(./linux/window-sandbox)'
fi
WINDOW_CMD="
  cd '$REPO_ROOT/electron' &&
  echo 'Waiting for UI...' &&
  until curl -s -o /dev/null '$UI_URL'; do sleep 1; done &&
  ./node_modules/.bin/electron . --dev --url='$UI_URL' $WINDOW_SANDBOX;
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
      mintty -t "$title" bash -c "$cmd" &
      ;;
    *)
      echo "Unsupported platform: $(uname -s)"
      exit 1
      ;;
  esac
}

case "$MODE" in
  tmux)
    tmux new-session -d -s vista-dev "$MCP_CMD; exec bash"
    tmux split-window -v "$BACKEND_CMD; exec bash"
    tmux split-window -h "$UI_CMD; exec bash"
    tmux attach -t vista-dev
    ;;
  terminal)
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
        case "$(uname -s)" in
          MINGW*|MSYS*|CYGWIN*)
            local proc
            for proc in /proc/[0-9]*; do
              [[ "$(cat "$proc/pgid" 2>/dev/null)" == "$pid" ]] || continue
              taskkill //F //T //PID "$(cat "$proc/winpid")" >/dev/null 2>&1 || true
            done
            ;;
          *)
            kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
            ;;
        esac
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
    echo "  MCP server: $LOG_DIR/mcp.log"
    echo "  Backend:    $LOG_DIR/backend.log"
    echo "  UI:         $LOG_DIR/ui.log"
    echo "Press Ctrl-C to stop all services."

    run_service backend "$LOG_DIR/backend.log" "$BACKEND_CMD"
    run_service ui "$LOG_DIR/ui.log" "$UI_CMD"
    run_service mcp "$LOG_DIR/mcp.log" "$MCP_CMD"

    if [[ -n "$ELECTRON" ]]; then
      echo "  Window:     $LOG_DIR/window.log (close it to stop everything)"
      run_service window "$LOG_DIR/window.log" "$WINDOW_CMD"
      # Only the window ends the session; the EXIT trap then stops the rest.
      # A window that fails says so, and the failure is the script's status.
      window_status=0
      wait "${pids[${#pids[@]}-1]}" || window_status=$?
      if [[ "$window_status" != 0 ]]; then
        echo "The VISTA window exited with status $window_status; see $LOG_DIR/window.log" >&2
      fi
      exit "$window_status"
    fi

    wait
    ;;
  *)
    echo "Usage: $0 [tmux|terminal|logs]"
    exit 1
    ;;
esac
