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
#
#   VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN
#   VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN
#                       Globus credentials for file transfer to Odo and
#                       Frontier. With neither set, those two clusters' file
#                       operations are unavailable and everything else runs
#                       normally. The first start with one set asks for a
#                       one-time Globus login in this terminal.

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
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
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

# ─── hardware virtualisation ────────────────────────────────────────────────

# The code-execution sandbox runs each agent session in a microVM. On macOS it
# uses the Hypervisor framework, which is always present. On Linux it needs
# KVM, which is not: a bare-metal workstation has it, a cloud VM needs nested
# virtualisation switched on, and access is usually group-gated.
#
# This refuses to start, and the reason is stronger than "one tool is missing".
# `dev_mcp_server`'s lifespan spawns a sandbox eagerly at startup
# (`server.py:79`), so without KVM that server never finishes `initialize`, the
# backend's MCP client sees `Connection closed`, and *every* agent tool call
# fails -- retrieval included, even though retrieval never touches the sandbox.
# Measured, not assumed: `rag_search` returns HTTP 500 on a host with no
# /dev/kvm while the MCP server itself has the store open and reports 4401
# chunks. Serving pages while the agent cannot answer anything is worse than
# saying so up front.
#
# VISTA_ALLOW_NO_KVM exists for the build's own smoke test, which runs inside a
# container where /dev/kvm is never present. It is not a way to use VISTA
# without KVM; the checks that depend on the agent are skipped when it is set.
if [[ "$HOST_OS" == linux && "${VISTA_ALLOW_NO_KVM:-}" != 1 ]]; then
  kvm_problem=''
  if [[ ! -e /dev/kvm ]]; then
    kvm_problem="this machine has no /dev/kvm.

  On bare metal, enable virtualisation (VT-x or AMD-V) in the firmware and \
check that the kvm module is loaded. Inside a virtual machine, the host has to \
expose nested virtualisation to it."
  elif [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
    kvm_problem="this account cannot read or write /dev/kvm.

  It is usually owned by the kvm group. Add yourself with \
\`sudo usermod -aG kvm ${USER:-$(id -un)}\`, then log out and back in so the \
new group takes effect."
  fi
  if [[ -n "$kvm_problem" ]]; then
    die "VISTA needs hardware virtualisation on Linux, and $kvm_problem

  Every agent tool call depends on it, not just running code: the sandbox
  server is started as part of the agent's toolset, so without it retrieval
  fails too."
  fi
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
# The Globus endpoint's image. A second one on purpose: the agent executes
# arbitrary generated code in `vista-sandbox`, and a long-lived process holding
# a credential that authorises moving the researcher's files cannot share it.
# The tag matches `IMAGE` in vista_mcp_server/lib/gcp_vm.py, which is what runs
# the microVM this imports the image for.
GLOBUS_IMAGE="vista-globus:latest"

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
  -path '*/microsandbox/_bundled/bin/msb' -print -quit 2>/dev/null)"
# The Globus endpoint runs under this same binary, and finds it by repeating
# this search from inside `vista_mcp_server`. Handing it the path found here
# means the two cannot end up using different runtimes, and so cannot disagree
# about which one holds the images imported below. An empty value, when the
# search found nothing, leaves it to search for itself and report the failure.
export VISTA_MSB_PATH="$MSB"

# Imports one image from the payload. The `image inspect` guard is what makes a
# second run cheap: the load costs a minute of disk on a first start and nothing
# at all afterwards. Nothing to do -- no runtime, or no such archive -- is not a
# failure; the caller decides what an actual failed load means, because the two
# images differ there.
load_image() {
  local tar="$1" tag="$2" what="$3"
  [[ -x "$MSB" && -f "$tar" ]] || return 0
  "$MSB" image inspect --format=json "$tag" >/dev/null 2>&1 && return 0
  log "First run: importing $what..."
  "$MSB" load -i "$tar" -t "$tag" >> "$LOGS/setup.log" 2>&1
}

load_image "$PACKAGE/payload/sandbox-image.tar" "$VISTA_DEV_MCP_IMAGE" \
  "the code-execution sandbox image" \
  || die "could not import the sandbox image; see $LOGS/setup.log"

# ─── Globus file transfer ───────────────────────────────────────────────────

# Globus Transfer moves files for Odo and Frontier. Perlmutter never touches it
# -- every file operation there goes through the NERSC IRI filesystem API -- so
# a researcher using only Perlmutter needs none of this.
#
# Gated *and* non-fatal, which are two different decisions. Gated because the
# endpoint authenticates with a refresh token, so without one it is a browser
# login asked of a researcher who has nothing to use it for. Non-fatal because
# passing the gate is not the same as being able to finish: no network, no
# terminal to log in from, or a declined login.
#
# Deliberately not the hard gate hardware virtualisation gets above. That one
# refuses to start because the sandbox server is spawned as part of the agent's
# toolset, so without it every tool call fails, retrieval included. Absent file
# transfer costs exactly two clusters, while chat, retrieval, the code sandbox
# and Perlmutter are untouched -- refusing to start would cost far more than it
# protects.
TRANSFER_CLUSTERS="Odo and Frontier"
# Empty once the endpoint is running, and otherwise the reason it is not, in
# words fit to print. Each branch below is a distinct cause with a distinct
# remedy, which is the whole point of naming one: a researcher who exported a
# token and still cannot transfer needs to know whether it was the token, the
# image, the login, or the endpoint itself.
TRANSFER_DETAIL="no Globus refresh token is configured"
GLOBUS_SETUP_DONE=false

# `scripts/` is not installed here, so the module is run directly. It is the
# same entry point `scripts/launch_globus.py` calls in a development checkout,
# which is what keeps the two from drifting.
globus() {
  "$PACKAGE/app/mcp_servers/vista_mcp_server/.venv/bin/python" \
    -m vista_mcp_server.lib.gcp_vm "$@"
}

if [[ -n "${VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN:-}" \
   || -n "${VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN:-}" ]]; then
  # Imported under the gate rather than beside the sandbox image, and its
  # failure warned about rather than fatal, so that an image most installations
  # never use can neither delay nor prevent a start.
  if ! load_image "$PACKAGE/payload/globus-image.tar" "$GLOBUS_IMAGE" \
       "the Globus file-transfer image"; then
    TRANSFER_DETAIL="the Globus image could not be imported; see $LOGS/setup.log"
  # Setup runs before any service starts, and on this terminal rather than into
  # a log file: it prints an address to log in at and waits for what the login
  # returns, so a researcher who cannot see it cannot complete it. Running it
  # here is also what makes a collection created on a first run visible to the
  # MCP server started below, in the same session.
  elif globus --setup; then
    GLOBUS_SETUP_DONE=true
    TRANSFER_DETAIL=''
  else
    TRANSFER_DETAIL="Globus endpoint setup did not complete"
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

# A fourth managed service, in PIDS so the trap above stops it. Nothing waits
# on it: it serves no port and has no health endpoint, and the two job tools
# that depend on it report its state themselves. It holds the microVM open, and
# a SIGTERM from `stop` unwinds it so the microVM goes with this launcher.
if [[ "$GLOBUS_SETUP_DONE" == true ]]; then
  globus --start > "$LOGS/globus.log" 2>&1 &
  GLOBUS_PID=$!
  PIDS+=("$GLOBUS_PID")
fi

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

# The endpoint serves no port, so having been started is not the same as
# running. `--status` asks the runtime, exits non-zero for every state but
# running, and prints the module's own account of why -- a microVM that died, an
# image that was never loaded. Only asked when there is something to ask about;
# the branches above already know the answer in every other case.
#
# Retried, because starting the endpoint means creating a microVM first, and
# reporting a transfer that is merely still coming up would be worse than
# waiting a moment. Bounded by the holder being alive: a process that has
# already exited will not produce a running endpoint.
if [[ -n "${GLOBUS_PID:-}" ]]; then
  # Defaulted rather than left to the status output, so that a status which
  # failed without saying anything -- the interpreter itself failing to start --
  # does not silently report transfer as available.
  TRANSFER_DETAIL="the Globus endpoint is not running"
  for (( i = 0; i < 3; i++ )); do
    if transfer_state="$(globus --status)"; then
      TRANSFER_DETAIL=''
      break
    fi
    TRANSFER_DETAIL="${transfer_state:-$TRANSFER_DETAIL}"
    kill -0 "$GLOBUS_PID" 2>/dev/null || break
    perl -e 'select(undef, undef, undef, 2)' 2>/dev/null || sleep 2
  done
fi

# Reported at startup rather than left to be discovered on the first transfer,
# which is a job submission that fails minutes later for a reason that was
# already knowable here.
if [[ -n "$TRANSFER_DETAIL" ]]; then
  log ""
  log "note: file transfer for $TRANSFER_CLUSTERS is unavailable:"
  log "      $TRANSFER_DETAIL."
  log "      Chat, retrieval, the code sandbox and Perlmutter are unaffected."
fi

log ""
log "VISTA is running at http://localhost:$UI_PORT"
log "Press Ctrl-C to stop."

wait
