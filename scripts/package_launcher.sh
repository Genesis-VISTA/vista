#!/usr/bin/env bash
# Start VISTA from an unpacked package. Installed at the package root as `vista`.
#
#   ./vista            first-run setup if needed, then start and open the window
#   ./vista --browser  the same, but print the address to open in a browser
#   ./vista --help     show this
#
# Closing the VISTA window stops VISTA, as does Ctrl-C here or closing this
# terminal. Where no window can be shown -- a package without one, or a
# session with no display, such as SSH -- it says so and behaves as --browser.
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
#   VISTA_BACKEND_FORUM__ENABLED
#                       The Hypothesis Lab (default: true). A project's lab
#                       still needs its own repository, set in the project's
#                       settings, and git 2.34 or later on PATH. Set false to
#                       turn the lab off for every project.
#
#   VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN
#   VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN
#   VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN
#   VISTA_MCP_FRONTIER_GLOBUS_HTTPS_REFRESH_TOKEN
#                       Deployment-wide Globus credentials for file transfer to
#                       Odo and Frontier, for a hosted install where one
#                       identity serves everyone. On a desktop, connect Globus
#                       in the VISTA user settings instead: nothing to export,
#                       and nothing to set up in this terminal.

set -euo pipefail

PACKAGE="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" && pwd)"
STATE="${VISTA_HOME:-$HOME/.vista}"

UI_PORT="${VISTA_UI_PORT:-3000}"
MCP_PORT="${VISTA_MCP_PORT:-8000}"
BACKEND_PORT="${VISTA_BACKEND_PORT:-8001}"

VERSION="$(cat "$PACKAGE/VERSION" 2>/dev/null || echo unknown)"

die() { echo "error: $*" >&2; exit 1; }
log() { printf '%s\n' "$*"; }

BROWSER_MODE=false
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
      exit 0
      ;;
    --browser) BROWSER_MODE=true ;;
    *) die "unexpected argument: $arg (try --help)" ;;
  esac
done

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

# ─── quarantine ─────────────────────────────────────────────────────────────

# macOS tags anything that arrived by browser download, AirDrop, or similar
# with com.apple.quarantine and propagates it to every file when the archive
# is unpacked through Finder or Archive Utility. Gatekeeper then blocks every
# bundled executable -- python, npm, msb, node -- the first time this script
# tries to run one.
#
# Only the quarantine attribute is removed, not `xattr -c` (clear all): msb's
# ad-hoc code signature also lives in an extended attribute, which
# build_local_package.sh's create_archive() goes out of its way to preserve
# through packaging. Clearing every xattr here would trade a quarantine
# failure for an invalid-signature one on the same binary.
if [[ "$HOST_OS" == macos ]]; then
  xattr -dr com.apple.quarantine "$PACKAGE" 2>/dev/null || true
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
# macOS and BSD, 108 on Linux. msb 0.7 adds about 50 bytes of its own beneath
# the store and rejects any socket path of 102 bytes or more, so a store path
# past 51 characters makes the sandbox unstartable (measured with msb 0.7.2).
#
# Reported here because the alternative is discovering it on the first agent
# message, as `InvalidConfigError: agent relay socket path is too long`, long
# after startup said everything was fine. The default `~/.vista` is around 30
# bytes; this only bites a deliberately deep `VISTA_HOME`.
MSB_STORE="$STATE/microsandbox"
SOCKET_BUDGET=51
if (( ${#MSB_STORE} > SOCKET_BUDGET )); then
  die "the state directory path is too long for the code-execution sandbox:
    $MSB_STORE
  is ${#MSB_STORE} characters and has to be at most $SOCKET_BUDGET. The sandbox \
runtime appends about 50 bytes to it to build a Unix socket path, which the \
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
# The backend keeps the Hypothesis Lab off unless told otherwise, and a
# development checkout turns it on in `.env`, which a package never reads.
# On here, because each project is still gated on its own repository and a
# usable git; the caller's own value wins.
export VISTA_BACKEND_FORUM__ENABLED="${VISTA_BACKEND_FORUM__ENABLED:-true}"
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

# Imports the sandbox image from the payload -- the only one the package ships.
# The `image inspect` guard is what makes a second run cheap: the load costs a
# minute of disk on a first start and nothing at all afterwards. Nothing to do
# -- no runtime, or no such archive -- is not a failure, and is left to the
# caller to judge.
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

# ─── services ───────────────────────────────────────────────────────────────

# Every service is started as the leader of its own process group, so stopping
# it reaches everything it started, not only the process we hold a PID for. The
# backend starts a sandbox server through `uv run` for each agent session, and
# that starts microVMs: killed by PID alone, those outlive the launcher and hold
# ports, and the next start refuses with "port in use".
set -m

PIDS=()
STOP_GRACE_SECONDS=10

# Signals every group, waits for them to go, then kills what is left. Services
# are stopped in reverse start order, so the window closes first and the MCP
# server, which the others talk to, last.
#
# HUP is trapped with the rest because closing the Terminal window sends it,
# and every output line here is guarded because that terminal may already be
# gone: a write error inside the trap would otherwise abort it under `set -e`
# with the services still running.
#
# A service's own group is not always enough. The backend's MCP client starts
# each sandbox server (`uv run dev-mcp-server`, its Python, its `msb`) in a new
# group of its own. On TERM the backend closes those itself, but if it has to
# be killed they would be orphaned. So every group below each service is
# collected first, while the process tree still links them, and anything left
# in any of them after the grace period is killed.
descendant_groups() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do
    ps -o pgid= -p "$child" 2>/dev/null | tr -d ' '
    descendant_groups "$child"
  done
}

STOPPING=false
stop() {
  trap - INT TERM HUP EXIT
  STOPPING=true
  { log ""; log "Stopping VISTA..."; } 2>/dev/null || true
  # Job control reports each service it sees die ("line 301: 78461
  # Terminated: 15 ..."), which reads like a failure. Nothing after this point
  # has anything to say on stderr, and this is the script's last act.
  exec 2>/dev/null
  local i pid group groups=() own_group
  # Never our own group: that one holds the shell this was started from.
  own_group="$(ps -o pgid= -p $$ | tr -d ' ')"
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    groups+=("$pid")
    for group in $(descendant_groups "$pid"); do
      [[ "$group" == "$own_group" || " ${groups[*]} " == *" $group "* ]] || groups+=("$group")
    done
  done
  for (( i = ${#PIDS[@]} - 1; i >= 0; i-- )); do
    pid="${PIDS[i]}"
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  done
  local waited=0 alive
  while (( waited < STOP_GRACE_SECONDS * 4 )); do
    alive=false
    for group in ${groups[@]+"${groups[@]}"}; do
      kill -0 -- "-$group" 2>/dev/null && alive=true && break
    done
    [[ "$alive" == true ]] || break
    perl -e 'select(undef, undef, undef, 0.25)' 2>/dev/null || sleep 1
    waited=$(( waited + 1 ))
  done
  for group in ${groups[@]+"${groups[@]}"}; do
    kill -KILL -- "-$group" 2>/dev/null || true
  done
  wait ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null || true
}
trap stop INT TERM HUP EXIT

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

# ─── window or address ──────────────────────────────────────────────────────

# 127.0.0.1, not localhost: it is what the UI binds, and localhost can resolve
# to ::1 first. It is also the origin the window's storage is kept under, so it
# has to be the same on every run.
UI_URL="http://127.0.0.1:$UI_PORT"

# Whether this session can show a window at all, with the reason on stdout when
# it cannot. One branch per OS, so a port adds a case rather than reworking this.
can_show_window() {
  case "$HOST_OS" in
    macos)
      # "Aqua" is a GUI login session; SSH and other background sessions
      # report something else, and a window started there never appears.
      local session
      session="$(launchctl managername 2>/dev/null || true)"
      [[ "$session" == Aqua ]] && return 0
      echo "this session has no display (launchctl reports '${session:-nothing}', not Aqua; over SSH, for example)"
      return 1
      ;;
    linux)
      # linux-desktop-window D4. X forwarding would put the window on the far
      # end of an SSH session, and someone there wants the address anyway.
      if [[ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ]]; then
        echo "this is a remote shell session"
        return 1
      fi
      if [[ "$(id -u)" == 0 ]]; then
        echo "the window does not run as root"
        return 1
      fi
      if [[ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]]; then
        echo "there is no graphical display (neither DISPLAY nor WAYLAND_DISPLAY is set)"
        return 1
      fi
      # D6: name what is missing rather than start a window that cannot load.
      # A desktop has all of these; a minimal server or container may not.
      # A bare system lacks all 26 at once, so only the first few are named.
      local missing
      missing="$(ldd "$PACKAGE/$WINDOW_EXE" 2>/dev/null | awk '/not found/ { print $1 }' | sort -u \
        | awk '{ n++; if (n <= 5) names = names (n > 1 ? " " : "") $1 }
               END { if (n) printf "%s%s", names, (n > 5 ? " and " (n - 5) " more" : "") }')"
      if [[ -n "$missing" ]]; then
        echo "the window needs system libraries this host lacks ($missing); on Ubuntu or Debian install libgtk-3-0t64 libnss3 libasound2t64 libgbm1, on Fedora or RHEL gtk3 nss alsa-lib mesa-libgbm"
        return 1
      fi
      return 0
      ;;
    *)
      echo "VISTA has no window on $HOST_OS yet"
      return 1
      ;;
  esac
}

# D1: the arguments the window needs on this host -- nothing, or --no-sandbox
# where the host blocks Chromium's sandbox. window-sandbox says why, and on
# Ubuntu how to turn it back on, and that is shown on every start that needs it.
window_sandbox_args() {
  [[ "$HOST_OS" == linux ]] || return 0
  local script
  script="$(dirname "$PACKAGE/$WINDOW_EXE")/window-sandbox"
  [[ -x "$script" ]] || return 0
  "$script" 2> "$LOGS/window-sandbox.log" || true
}

# The package says where its window is; the launcher does not assume a layout.
WINDOW_EXE="$(manifest_field exe)"
WINDOW_PID=''
if [[ "$BROWSER_MODE" != true ]]; then
  if [[ -z "$WINDOW_EXE" || ! -x "$PACKAGE/$WINDOW_EXE" ]]; then
    log "This package has no VISTA window; open the address below in a browser."
  elif ! reason="$(can_show_window)"; then
    log "Not opening the VISTA window: $reason."
  else
    WINDOW_ARGS=()
    sandbox_arg="$(window_sandbox_args)"
    if [[ -n "$sandbox_arg" ]]; then
      WINDOW_ARGS+=("$sandbox_arg")
      log ""
      while IFS= read -r line; do log "$line"; done < "$LOGS/window-sandbox.log"
    fi
    "$PACKAGE/$WINDOW_EXE" ${WINDOW_ARGS[@]+"${WINDOW_ARGS[@]}"} --url="$UI_URL" > "$LOGS/window.log" 2>&1 &
    WINDOW_PID=$!
    PIDS+=("$WINDOW_PID")
  fi
fi

log ""
if [[ -n "$WINDOW_PID" ]]; then
  log "VISTA is open in its own window ($UI_URL)."
  log "Close the window, or press Ctrl-C here, to stop."
  # The window closing or quitting ends the session; the EXIT trap stops the
  # rest. A trapped signal interrupts this wait and runs `stop` first, and the
  # script then carries on here -- so STOPPING, not the status, says whether it
  # was a stop.
  window_status=0
  wait "$WINDOW_PID" || window_status=$?
  if [[ "$window_status" == 0 || "$STOPPING" == true ]]; then
    exit 0
  fi
  # D5: a window that fails -- at start or mid-session -- is not a reason to
  # stop the services under it. Carry on as browser mode does.
  log ""
  log "The VISTA window stopped unexpectedly (exit $window_status); see $LOGS/window.log."
  log "The services are still running."
fi

log "VISTA is running at $UI_URL"
log "Press Ctrl-C to stop."

wait
