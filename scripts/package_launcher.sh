#!/usr/bin/env bash
# Start VISTA from an unpacked package. Installed at the package root as `vista`.
#
#   ./vista            first-run setup if needed, then start
#   ./vista --help     show this
#
# Everything the running system needs is inside this directory. Nothing is
# installed, downloaded, or configured on the machine: the only thing outside
# the package is the state directory, which holds the database, uploads, the
# corpus, and the sandbox image store.
#
# Environment:
#   VISTA_HOME          state directory (default: ~/.vista)
#   VISTA_UI_PORT       default 3000
#   VISTA_MCP_PORT      default 8000
#   VISTA_BACKEND_PORT  default 8001

set -euo pipefail

PACKAGE="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" && pwd)"
STATE="${VISTA_HOME:-$HOME/.vista}"

UI_PORT="${VISTA_UI_PORT:-3000}"
MCP_PORT="${VISTA_MCP_PORT:-8000}"
BACKEND_PORT="${VISTA_BACKEND_PORT:-8001}"

VERSION="$(cat "$PACKAGE/VERSION" 2>/dev/null || echo unknown)"

die() { echo "error: $*" >&2; exit 1; }
log() { printf '%s\n' "$*"; }

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  sed -n '2,16p' "$0" | sed -E 's/^# ?//'
  exit 0
fi
[[ $# -eq 0 ]] || die "unexpected argument: $1 (try --help)"

# ─── platform guard ─────────────────────────────────────────────────────────

# Checked before anything else, and with `sed` rather than the bundled
# interpreter: on the wrong platform that interpreter is exactly what cannot
# run, so a guard that used it would fail with a confusing exec error instead of
# saying what is wrong.
manifest_field() {
  sed -nE "s/.*\"$1\": \"([^\"]+)\".*/\1/p" "$PACKAGE/manifest.json" | head -1
}

BUILT_OS="$(manifest_field os)"
BUILT_ARCH="$(manifest_field arch)"
case "$(uname -s)" in
  Darwin) HOST_OS=macos ;;
  Linux)  HOST_OS=linux ;;
  *)      HOST_OS="$(uname -s)" ;;
esac
HOST_ARCH="$(uname -m)"

if [[ -n "$BUILT_OS" && ( "$HOST_OS" != "$BUILT_OS" || "$HOST_ARCH" != "$BUILT_ARCH" ) ]]; then
  die "this package was built for ${BUILT_OS}-${BUILT_ARCH}, but this machine is \
${HOST_OS}-${HOST_ARCH}. Interpreters and compiled libraries inside it cannot run here; \
use the ${HOST_OS}-${HOST_ARCH} build."
fi

# ─── port preflight ─────────────────────────────────────────────────────────

# Reported before any service starts. Otherwise the conflict surfaces as a
# health poll that never answers, which looks like a hang and says nothing
# about the cause.
#
# Probed with bash's own /dev/tcp rather than lsof, which is absent on some
# minimal systems and reports differently across platforms.
port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1 && exec 3<&- && return 0
  return 1
}

conflicts=()
for entry in "$UI_PORT|the web interface" "$MCP_PORT|the MCP server" \
             "$BACKEND_PORT|the backend"; do
  port="${entry%%|*}"
  role="${entry#*|}"
  port_in_use "$port" && conflicts+=("port $port ($role) is already in use")
done
if (( ${#conflicts[@]} > 0 )); then
  echo "error: VISTA cannot start:" >&2
  for conflict in "${conflicts[@]}"; do
    echo "  - $conflict" >&2
  done
  echo "Stop whatever is using it, or set VISTA_UI_PORT / VISTA_MCP_PORT /" >&2
  echo "VISTA_BACKEND_PORT to different ports." >&2
  exit 1
fi

# ─── state path length ──────────────────────────────────────────────────────

# The sandbox runtime derives a Unix domain socket path from its store
# directory, and those have a hard length limit in the kernel -- 104 bytes on
# macOS and BSD, 108 on Linux. msb adds about 40 bytes of its own beneath the
# store, so a state directory much past 60 characters makes the socket
# unaddressable.
#
# Reported here because the alternative is discovering it on the first agent
# message, as `InvalidConfigError: agent relay socket path is too long`, long
# after startup said everything was fine. The default `~/.vista` is around 30
# bytes; this only bites a deliberately deep `VISTA_HOME`.
MSB_STORE="$STATE/microsandbox"
SOCKET_BUDGET=60
if (( ${#MSB_STORE} > SOCKET_BUDGET )); then
  die "the state directory path is too long for the code-execution sandbox:
    $MSB_STORE
  is ${#MSB_STORE} characters and has to be at most $SOCKET_BUDGET. The sandbox \
runtime appends about 40 bytes to it to build a Unix socket path, which the \
kernel caps at 104 bytes. Set VISTA_HOME to a shorter directory -- the default, \
~/.vista, is about 30 -- and re-run."
fi

# ─── environment ────────────────────────────────────────────────────────────

# Every path is derived from this script's own location, so the package works
# from any working directory and after being moved.
#
# `VISTA_MCP_LOCAL_HPC_JOBS_DIR` in particular is not optional:
# `vista_mcp_server/config.py` defaults it to a working-directory-relative
# `../../hpc_jobs`, and `submit_job_mcp.py` iterates that directory at *import*
# time -- so the server raises `FileNotFoundError` before its lifespan runs
# when started from anywhere else. `VISTA_HPC_JOBS_DIR` and
# `VISTA_BUILD_RAG_DIR` are the same class of problem on the backend side,
# where the paths are derived by walking up from a module's own file and land
# inside the virtual environment once the project is installed non-editably.
export PATH="$PACKAGE/bin:$PACKAGE/node/bin:$PATH"
# The bundled `uv` is a runtime dependency: the backend spawns the sandbox MCP
# server with `uv run` on every agent session. UV_NO_SYNC stops it deciding a
# relocated environment is stale and attempting a reinstall mid-session, which
# on a machine with no network would fail and take the session with it.
export UV_NO_SYNC=1
export VISTA_VERSION="$VERSION"
export VISTA_DATA_DIR="$STATE"
export VISTA_MCP_SERVERS_PATH="$PACKAGE/app/mcp_servers"
export VISTA_MCP_LOCAL_HPC_JOBS_DIR="$PACKAGE/app/hpc_jobs"
export VISTA_HPC_JOBS_DIR="$PACKAGE/app/hpc_jobs"
export VISTA_BUILD_RAG_DIR="$PACKAGE/app"
export VISTA_DATA_PAYLOAD_DIR="$STATE/vista-data"
export VISTA_MCP_URL="http://127.0.0.1:$MCP_PORT/mcp"
export VISTA_BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"
export VISTA_BACKEND_PORT="$BACKEND_PORT"
export HF_HOME="$STATE/huggingface"
# A cache miss must fail loudly rather than quietly reaching the network: the
# weights ship inside the package precisely so this works offline.
export HF_HUB_OFFLINE=1
export MSB_HOME="$MSB_STORE"
# Use the sandbox image already imported below, rather than building one.
# `dev_mcp_server` defaults `dockerfile` to the file inside its own package, and
# the microsandbox backend reads a dockerfile as "build this with docker or
# podman first" -- so on a machine with neither, the first agent session dies
# with `docker or podman not found on PATH` even though the image is present.
export VISTA_DEV_MCP_DOCKERFILE=""
export VISTA_DEV_MCP_IMAGE="vista-sandbox:latest"

LOGS="$STATE/logs"

# ─── first-run setup ────────────────────────────────────────────────────────

mkdir -p "$STATE" "$LOGS"

FIRST_RUN=false
[[ -f "$STATE/vista.db" ]] || FIRST_RUN=true

# The payload is copied out of the package rather than read in place, for two
# reasons: the knowledge-base row records absolute paths, so reading in place
# would break when the package is replaced; and the corpus is the researcher's
# to add to. Each part is skipped when already present, which is what makes a
# second run cheap and an upgrade a directory replacement.
for part in vista-data knowledge-bases huggingface; do
  if [[ -d "$PACKAGE/payload/$part" && ! -e "$STATE/$part" ]]; then
    log "First run: installing $part..."
    cp -R "$PACKAGE/payload/$part" "$STATE/$part"
  fi
done

MSB="$(find "$PACKAGE/app/mcp_servers/dev_mcp_server/.venv" \
  -path '*/microsandbox/_bundled/bin/msb' 2>/dev/null | head -1)"
if [[ -x "$MSB" && -f "$PACKAGE/payload/sandbox-image.tar" ]]; then
  if ! "$MSB" image inspect --format=json "$VISTA_DEV_MCP_IMAGE" >/dev/null 2>&1; then
    log "First run: importing the code-execution sandbox image..."
    "$MSB" load -i "$PACKAGE/payload/sandbox-image.tar" -t "$VISTA_DEV_MCP_IMAGE" \
      >> "$LOGS/setup.log" 2>&1 \
      || die "could not import the sandbox image; see $LOGS/setup.log"
  fi
fi

# ─── services ───────────────────────────────────────────────────────────────

PIDS=()
stop() {
  trap - INT TERM EXIT
  log ""
  log "Stopping VISTA..."
  local pid
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill "$pid" 2>/dev/null || true
  done
  wait ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null || true
}
trap stop INT TERM EXIT

wait_for() {
  local url="$1" seconds="$2" logfile="$3" what="$4" i
  for (( i = 0; i < seconds; i++ )); do
    curl -s -o /dev/null -m 5 "$url" && return 0
    perl -e 'select(undef, undef, undef, 1)' 2>/dev/null || sleep 1
  done
  echo >&2
  echo "error: $what did not start within ${seconds}s. Last lines of $logfile:" >&2
  tail -15 "$logfile" >&2
  exit 1
}

# Each service writes to its own file rather than interleaving on stdout: three
# services' output braided together is unreadable, and the one thing a
# researcher needs from a successful start is the address.
log "VISTA $VERSION"
log "Starting services (logs in $LOGS)..."

"$PACKAGE/app/mcp_servers/vista_mcp_server/.venv/bin/vista-mcp-server" \
  --transport=http --port "$MCP_PORT" > "$LOGS/mcp.log" 2>&1 &
PIDS+=($!)
wait_for "$VISTA_MCP_URL" 180 "$LOGS/mcp.log" "the MCP server"

if [[ "$FIRST_RUN" == true ]]; then
  log "First run: preparing the database and corpus (this takes a minute)..."
fi
"$PACKAGE/app/backend/.venv/bin/vista-backend" > "$LOGS/backend.log" 2>&1 &
PIDS+=($!)
wait_for "$VISTA_BACKEND_URL/openapi.json" 600 "$LOGS/backend.log" "the backend"

PORT="$UI_PORT" HOSTNAME=127.0.0.1 "$PACKAGE/node/bin/node" \
  "$PACKAGE/app/ui/server.js" > "$LOGS/ui.log" 2>&1 &
PIDS+=($!)
wait_for "http://127.0.0.1:$UI_PORT/" 120 "$LOGS/ui.log" "the web interface"

log ""
log "VISTA is running at http://localhost:$UI_PORT"
log "Press Ctrl-C to stop."

wait
