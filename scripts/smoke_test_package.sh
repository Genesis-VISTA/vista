#!/usr/bin/env bash
# Start an unpacked VISTA package, check it works, and shut it down.
#
#   ./scripts/smoke_test_package.sh <unpacked-package> [state-dir]
#
# Run by `build_local_package.sh` against a copy unpacked at a different path
# depth, which is what catches a path baked in at build time. Also usable by
# hand on any unpacked package.
#
# Everything here that prepares state -- copying the payload, importing the
# sandbox image, pinning the paths whose defaults are relative to the working
# directory -- is what the `vista` launcher will do for real. Keeping it in one
# script until then means the smoke test exercises the same steps.

set -euo pipefail

PACKAGE="${1:?usage: smoke_test_package.sh <unpacked-package> [state-dir]}"
STATE="${2:-$PACKAGE/state}"
PACKAGE="$(cd "$PACKAGE" && pwd)"

# High ports, so a smoke test never collides with a running dev instance.
MCP_PORT="${VISTA_SMOKE_MCP_PORT:-18000}"
BACKEND_PORT="${VISTA_SMOKE_BACKEND_PORT:-18001}"
UI_PORT="${VISTA_SMOKE_UI_PORT:-13000}"

LOGS="$STATE/logs"
PIDS=()
FAILED=0

die() { echo "error: $*" >&2; exit 1; }
log() { printf '\n--> %s\n' "$*"; }
check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  ok   $label"
  else
    echo "  FAIL $label" >&2
    FAILED=1
  fi
}

# Distinct from `ok` on purpose. A check that could not run here is not a check
# that passed, and the build's output should not let the two be confused.
skip() {
  echo "  skip $1 -- $2"
}

IS_WINDOWS=false
case "$(uname -s)" in
  MINGW*|MSYS*) IS_WINDOWS=true ;;
esac

# The package's own interpreter, for the checks below that parse JSON.
PACKAGE_PYTHON="$PACKAGE/app/backend/.venv/bin/python"
[[ "$IS_WINDOWS" == true ]] && PACKAGE_PYTHON="$PACKAGE/app/backend/.venv/Scripts/python.exe"

cleanup() {
  trap - INT TERM EXIT
  local pid
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    if [[ "$IS_WINDOWS" == true ]]; then
      # The launcher is native PowerShell, and bash's signals reach neither it
      # nor the services it started; taskkill /T takes the whole tree.
      taskkill //F //T //PID "$(cat "/proc/$pid/winpid" 2>/dev/null)" >/dev/null 2>&1 || true
    else
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null || true
}
trap cleanup INT TERM EXIT

wait_for() {
  local url="$1" seconds="${2:-120}" i
  for (( i = 0; i < seconds; i++ )); do
    curl -s -o /dev/null -m 5 "$url" && return 0
    perl -e 'select(undef, undef, undef, 1)'
  done
  return 1
}

# ─── run it the way a researcher would ──────────────────────────────────────

# The package's own launcher is what gets exercised, rather than a second copy
# of its logic: first-run setup, the path pinning, the sandbox image import and
# the service ordering all live there, and a smoke test that reimplemented them
# would be testing itself. The diagnostic launcher starts the services for the
# checks below; the application's own supervised mode is checked after them, the
# same way on every platform (desktop-app-startup D12).
WINDOW_EXE="$(
  "$PACKAGE_PYTHON" -c \
    'import json,sys; w=json.load(open(sys.argv[1], encoding="utf-8")).get("window"); print(w["exe"] if w else "")' \
    "$PACKAGE/manifest.json"
)"
LAUNCHER=("$PACKAGE/vista")
SUPERVISED_ARGS=(--supervised --progress=jsonl)
if [[ "$IS_WINDOWS" == true ]]; then
  [[ -f "$PACKAGE/vista.ps1" ]] || die "no launcher at $PACKAGE/vista.ps1"
  LAUNCHER=(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PACKAGE/vista.ps1")
  SUPERVISED_ARGS=(-Supervised -Progress jsonl)
else
  [[ -x "$PACKAGE/vista" ]] || die "no launcher at $PACKAGE/vista"
fi
export VISTA_NO_WINDOW=1

export VISTA_HOME="$STATE"

# VISTA always needs a microVM, so by default a host where the launcher refuses
# to start for want of one cannot verify a package, and this test fails there.
# The one opt-out is VISTA_VERIFY_WITHOUT_SANDBOX=1, which
# `build_local_package.sh --verify-without-sandbox` sets for hosts that cannot
# run the sandbox, such as hosted macOS CI runners. It reaches the launcher
# through this environment. Every check that goes through the sandbox is then
# reported as skipped, never as ok, and the result says the package was
# verified without it. Retrieval is one of them: the sandbox server is part of
# every agent toolset, so a call through the backend's MCP client fails without
# it.
WITHOUT_SANDBOX=false
[[ "${VISTA_VERIFY_WITHOUT_SANDBOX:-}" == 1 ]] && WITHOUT_SANDBOX=true
NO_SANDBOX_REASON="verified without the sandbox (VISTA_VERIFY_WITHOUT_SANDBOX=1); run the full smoke test on a machine that can run it"

export VISTA_UI_PORT="$UI_PORT"
export VISTA_MCP_PORT="$MCP_PORT"
export VISTA_BACKEND_PORT="$BACKEND_PORT"
BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"

mkdir -p "$STATE" "$LOGS"

log "starting the package launcher"
"${LAUNCHER[@]}" > "$LOGS/launcher.log" 2>&1 &
PIDS+=($!)

# The end of the launcher's output, and of setup.log, where first-run steps
# such as the sandbox image import write their errors. On a CI runner the
# smoke test's directory is gone with the job, so this is all there is.
show_launcher_logs() {
  local f
  for f in "$LOGS/launcher.log" "$LOGS/setup.log"; do
    [[ -s "$f" ]] || continue
    echo "--- last lines of ${f##*/} ---" >&2
    tail -20 "$f" >&2
  done
}

# The diagnostic launcher prints an address when its services are up.
READY_PATTERN='VISTA is running at'
for (( i = 0; i < 600; i++ )); do
  grep -q "$READY_PATTERN" "$LOGS/launcher.log" 2>/dev/null && break
  if ! kill -0 "${PIDS[0]}" 2>/dev/null; then
    show_launcher_logs
    die "VISTA exited before completing startup"
  fi
  perl -e 'select(undef, undef, undef, 1)'
done
grep -q "$READY_PATTERN" "$LOGS/launcher.log" \
  || { show_launcher_logs; die "VISTA never completed startup"; }

# ─── checks ─────────────────────────────────────────────────────────────────

log "checking"
http_ok() {
  local code
  code="$(curl -s -o /dev/null -m 20 -w '%{http_code}' "$1")"
  [[ "$code" == "200" ]]
}

check "mcp server responds"      curl -s -o /dev/null -m 10 "http://127.0.0.1:$MCP_PORT/mcp"
check "backend serves openapi"   http_ok "$BACKEND_URL/openapi.json"
check "ui serves its home page"  http_ok "http://127.0.0.1:$UI_PORT/"
check "ui reaches the backend"   http_ok "http://127.0.0.1:$UI_PORT/api/projects"

# Whether the build packed the science projects, as it recorded in the manifest.
# It decides which retrieval checks apply and whether the science data must be
# absent.
SCIENCE_PROJECTS="$(
  "$PACKAGE_PYTHON" -c \
    'import json,sys; print("true" if json.load(open(sys.argv[1], encoding="utf-8")).get("science_projects") else "false")' \
    "$PACKAGE/manifest.json"
)"

# Retrieval, through the backend's MCP client -- the same path the agent uses,
# so it covers the store, the embedding weights loading offline, and the
# knowledge base having actually been registered. Arguments: the project the
# call goes through, the knowledge base, and a query the corpus should answer.
retrieval_returns_passages() {
  local project="$1" kb_slug="$2" query="$3" body
  body="$(
    curl -s -m 120 -X POST \
      "$BACKEND_URL/projects/$project/mcp/call" \
      -H 'content-type: application/json' \
      -d "{\"name\":\"rag_search\",\"arguments\":{\"query\":\"$query\",\"kb_slug\":\"$kb_slug\"}}"
  )"
  echo "$body" > "$LOGS/retrieval-$kb_slug.json"
  "$PACKAGE_PYTHON" - "$LOGS/retrieval-$kb_slug.json" <<'PYCHECK'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
if result.get("isError"):
    sys.exit(f"rag_search reported an error: {result}")
text = "".join(
    part.get("text", "") for part in result.get("content", []) if isinstance(part, dict)
)
if len(text) < 200:
    sys.exit(f"rag_search returned no usable passages: {text[:300]!r}")
PYCHECK
}
if [[ "$WITHOUT_SANDBOX" == true ]]; then
  skip "AI-safety retrieval returns passages" "$NO_SANDBOX_REASON"
else
  check "AI-safety retrieval returns passages" retrieval_returns_passages \
    ai-safety-autonomous-labs ai-safety "memory poisoning in LLM agents"
fi

if [[ "$SCIENCE_PROJECTS" == true && "$WITHOUT_SANDBOX" == true ]]; then
  skip "molten-salt retrieval returns passages" "$NO_SANDBOX_REASON"
elif [[ "$SCIENCE_PROJECTS" == true ]]; then
  check "molten-salt retrieval returns passages" retrieval_returns_passages \
    molten-salt molten-salt-papers "thermal conductivity of molten fluoride salts"
fi

# A default build must carry no science data, in the package or in the state its
# first run installed: no MSTDB, no molten-salt corpus or index, no forge-tune
# CSV. Found by name anywhere under both trees, so a copy that landed somewhere
# unexpected still counts.
science_data_found() {
  find "$PACKAGE" "$STATE" \( \
      -path '*/vista-data/mstdb' -o \
      -path '*/vista-data/molten-salt-papers' -o \
      -path '*/knowledge-bases/molten-salt-papers' -o \
      -path '*/hpc_jobs/forge-tune/*.csv' -o \
      -name 'Molten_Salt_Thermophysical_Properties*' \
    \) -print > "$LOGS/science-data-found.txt" 2>/dev/null || true
  [[ -s "$LOGS/science-data-found.txt" ]]
}
if [[ "$SCIENCE_PROJECTS" == true ]]; then
  skip "the package carries no science data" "built with --science-projects"
else
  no_science_data() { ! science_data_found; }
  check "the package carries no science data" no_science_data
fi

# The launcher has nothing left to say about file transfer, and this asserts the
# silence. OLCF file operations are HTTPS requests made inside a tool call, so
# there is nothing to start at launch and a start that announced anything about
# it would be announcing a guess.
launcher_is_quiet_about_transfer() {
  ! grep -qi "globus\|file transfer" "$LOGS/launcher.log"
}
check "launcher says nothing about file transfer" launcher_is_quiet_about_transfer

# File transfer itself is never exercised here, and saying "ok" for a check that
# could not run is exactly what `skip` exists to prevent: with no credential
# there is nothing to authorize a request to OLCF.
skip "globus file operations work" \
  "no credential connected here; verify by connecting Globus in settings and fetching a job's output"

# The build identifier has to be the same in the manifest, the launcher output
# and the running service, so a researcher reporting a problem can say which
# build they have.
version_is_consistent() {
  local declared reported version_log="$LOGS/launcher.log"
  declared="$(cat "$PACKAGE/VERSION")"
  reported="$(
    curl -s -m 20 "$BACKEND_URL/openapi.json" \
      | "$PACKAGE_PYTHON" -c \
        'import json,sys; print(json.load(sys.stdin.buffer)["info"]["version"])'
  )"
  [[ -n "$declared" && "$declared" == "$reported" ]] \
    && grep -q "VISTA $declared" "$version_log"
}
check "version matches across manifest, launcher and app" version_is_consistent

# The window, as unpacked here -- relocated, and carrying the signature the
# build gave it through the archive round trip -- loads the running UI. Its
# --smoke-test mode never shows anything and takes no single-instance lock, so
# a VISTA the builder has open cannot turn this into a false failure. It still
# needs a GUI session to start at all, which a build over SSH does not have.
# On Linux it gets the sandbox arguments the launcher would give it on this
# host (linux-desktop-window D1), and a virtual display when there is no real
# one, which is how a build container runs it (D7).
WINDOW_RUNNER=()
window_loads_the_ui() {
  local sandbox=''
  if [[ "$(uname -s)" == Linux ]]; then
    sandbox="$("$PACKAGE/$(dirname "$WINDOW_EXE")/window-sandbox" 2>>"$LOGS/window-smoke.log")"
  fi
  # $sandbox is empty or the single word --no-sandbox, so it is left unquoted.
  # shellcheck disable=SC2086
  ${WINDOW_RUNNER[@]+"${WINDOW_RUNNER[@]}"} "$PACKAGE/$WINDOW_EXE" $sandbox \
    --smoke-test --url="http://127.0.0.1:$UI_PORT/" >> "$LOGS/window-smoke.log" 2>&1
}
if [[ -z "$WINDOW_EXE" ]]; then
  skip "the window loads the UI" "this package has no window"
elif [[ "$(uname -s)" == Darwin && "$(launchctl managername 2>/dev/null)" != Aqua ]]; then
  skip "the window loads the UI" "no GUI session here (SSH?); rerun from a logged-in desktop"
elif [[ "$(uname -s)" == Linux && -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] \
     && ! command -v xvfb-run >/dev/null 2>&1; then
  skip "the window loads the UI" "no display and no xvfb-run here; install xvfb, or rerun from a desktop session"
else
  if [[ "$(uname -s)" == Linux && -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    WINDOW_RUNNER=(xvfb-run -a)
  fi
  check "the window loads the UI" window_loads_the_ui
fi

log "shutting down"
cleanup
# cleanup disarms the trap; the supervised check below starts processes again.
PIDS=()
trap cleanup INT TERM EXIT

ports_are_free() {
  "$PACKAGE_PYTHON" - "$MCP_PORT" "$BACKEND_PORT" "$UI_PORT" <<'PYCHECK'
import socket
import sys

for raw_port in sys.argv[1:]:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        if sock.connect_ex(("127.0.0.1", int(raw_port))) == 0:
            raise SystemExit(f"port {raw_port} remains occupied")
PYCHECK
}
# Windows processes are not visible to Git Bash's own process table, so there
# the question goes to Windows, by executable path.
package_processes_are_gone() {
  local pid
  if [[ "$IS_WINDOWS" == true ]]; then
    local count
    count="$(powershell.exe -NoProfile -Command \
      "@(Get-Process | Where-Object { \$_.Path -and \$_.Path.StartsWith('$(cygpath -w "$PACKAGE")', 'OrdinalIgnoreCase') }).Count" \
      | tr -d '\r')"
    [[ "$count" == 0 ]] || { echo "$count package processes remain" >&2; return 1; }
    return 0
  fi
  while IFS= read -r pid; do
    [[ -z "$pid" || "$pid" == "$$" ]] && continue
    echo "package process $pid remains" >&2
    return 1
  done < <(pgrep -f "$PACKAGE" 2>/dev/null || true)
}

# ─── the application's supervised protocol ──────────────────────────────────

# What the application depends on, checked on every platform (D12): the
# launcher's supervised mode, which VISTA.exe, VISTA.app and vista-app all
# start. Its stdin comes from a loop that ends when $STOP_FILE appears, which
# closes the pipe: the end of stdin is the stop request on every platform, and
# a pipe, unlike a FIFO, reaches a native Windows process from Git Bash.
STOP_FILE="$STATE/supervised.stop"
supervised_input() {
  while [[ ! -e "$STOP_FILE" ]]; do perl -e 'select(undef, undef, undef, 0.2)'; done
}

# The events, in order, from a protocol-v1 stream: every phase starts and then
# completes or becomes ready, through ui/ready at the given URL.
events_run_in_order() {
  "$PACKAGE_PYTHON" - "$1" "$2" <<'PYCHECK'
import json
import sys

events = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
if any(event.get("protocol") != 1 for event in events):
    sys.exit("an event is not protocol 1")
seen = [(event["phase"], event["state"]) for event in events]
expected = [
    ("preflight", "running"), ("preflight", "complete"),
    ("resources", "running"), ("resources", "complete"),
    ("sandbox", "running"), ("sandbox", "complete"),
    ("mcp", "running"), ("mcp", "ready"),
    ("backend", "running"), ("backend", "ready"),
    ("ui", "running"), ("ui", "ready"),
]
if seen[: len(expected)] != expected:
    sys.exit(f"events out of order: {seen}")
if events[len(expected) - 1].get("url") != sys.argv[2]:
    sys.exit(f"ui is ready at {events[len(expected) - 1].get('url')!r}, not {sys.argv[2]!r}")
PYCHECK
}

log "checking the supervised protocol"
rm -f "$STOP_FILE"

# A port in use fails preflight with its code, before anything starts. The
# package's own interpreter holds the UI port meanwhile.
"$PACKAGE_PYTHON" -c '
import socket, sys, time
sock = socket.socket()
sock.bind(("127.0.0.1", int(sys.argv[1])))
sock.listen()
time.sleep(120)
' "$UI_PORT" &
PORT_HOLDER=$!
PIDS+=("$PORT_HOLDER")
for (( i = 0; i < 50; i++ )); do
  (exec 3<>"/dev/tcp/127.0.0.1/$UI_PORT") 2>/dev/null && break
  perl -e 'select(undef, undef, undef, 0.1)'
done
"${LAUNCHER[@]}" "${SUPERVISED_ARGS[@]}" < /dev/null \
  > "$LOGS/supervised-conflict.jsonl" 2> "$LOGS/supervised-conflict.log" || true
kill "$PORT_HOLDER" 2>/dev/null || true
wait "$PORT_HOLDER" 2>/dev/null || true
PIDS=()
conflict_is_reported() {
  [[ "$(tr -d '\r' < "$LOGS/supervised-conflict.jsonl" | tail -1)" \
    == '{"protocol":1,"phase":"preflight","state":"failed","code":"port-conflict"}' ]]
}
check "a port in use fails preflight with port-conflict" conflict_is_reported

supervised_input | "${LAUNCHER[@]}" "${SUPERVISED_ARGS[@]}" \
  > "$LOGS/supervised.jsonl" 2> "$LOGS/supervised.log" &
SUPERVISED_PID=$!
PIDS+=("$SUPERVISED_PID")
for (( i = 0; i < 600; i++ )); do
  grep -q '"phase":"ui","state":"ready"' "$LOGS/supervised.jsonl" 2>/dev/null && break
  kill -0 "$SUPERVISED_PID" 2>/dev/null || break
  perl -e 'select(undef, undef, undef, 1)'
done
check "supervised events run in order to ui/ready" \
  events_run_in_order "$LOGS/supervised.jsonl" "http://127.0.0.1:$UI_PORT"

# The stop request: stdin closes, and the launcher's own stop runs.
touch "$STOP_FILE"
for (( i = 0; i < 60; i++ )); do
  kill -0 "$SUPERVISED_PID" 2>/dev/null || break
  perl -e 'select(undef, undef, undef, 1)'
done
stopped_on_request() {
  ! kill -0 "$SUPERVISED_PID" 2>/dev/null \
    && grep -q '"phase":"stopping","state":"running"' "$LOGS/supervised.jsonl"
}
check "closing stdin stops the supervised launcher" stopped_on_request
# If closing stdin did not stop it, that check has failed; stop it anyway so
# nothing is left running.
kill -0 "$SUPERVISED_PID" 2>/dev/null && cleanup
check "the stop releases every service port" ports_are_free
check "the stop leaves no package process" package_processes_are_gone

if (( FAILED )); then
  echo >&2
  echo "error: smoke test failed; logs are in $LOGS" >&2
  exit 1
fi
if [[ "$WITHOUT_SANDBOX" == true ]]; then
  echo "all checks passed (verified without the sandbox)"
else
  echo "all checks passed"
fi
