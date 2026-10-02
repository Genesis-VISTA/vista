#!/bin/bash
set -euo pipefail

# Supervised source-checkout launcher for VISTA Dev.app. Stdout is reserved for
# the same protocol consumed from a release package; service output goes to the
# developer state directory instead.

if [[ "${1:-}" != "--supervised" || "${2:-}" != "--progress=jsonl" || $# -ne 2 ]]; then
  echo "usage: $0 --supervised --progress=jsonl" >&2
  exit 2
fi

PACKAGE_ROOT="$(cd "$(dirname "$0")" && pwd)"
ROOT_FILE="$PACKAGE_ROOT/dev-root"
STATE_DIR="${VISTA_HOME:-$HOME/.vista-dev}"
LOG_DIR="$STATE_DIR/logs"
STACK_LOG="$LOG_DIR/dev-stack.log"
STACK_PID=''
EOF_PID=''
SHUTDOWN_REQUESTED=false

emit() {
  local phase="$1" state="$2" suffix="${3:-}"
  printf '{"protocol":1,"phase":"%s","state":"%s"%s}\n' "$phase" "$state" "$suffix"
}

fail_phase() {
  local phase="$1" code="$2" log="${3:-}"
  if [[ -n "$log" ]]; then
    emit "$phase" failed ",\"code\":\"$code\",\"log\":\"$log\""
  else
    emit "$phase" failed ",\"code\":\"$code\""
  fi
  exit 1
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP
  if [[ "$SHUTDOWN_REQUESTED" == true ]]; then
    emit stopping running ',"label":"Stopping VISTA"'
  fi
  if [[ -n "$STACK_PID" ]]; then
    kill -TERM "$STACK_PID" 2>/dev/null || true
    wait "$STACK_PID" 2>/dev/null || true
  fi
  if [[ -n "$EOF_PID" ]]; then
    kill "$EOF_PID" 2>/dev/null || true
    wait "$EOF_PID" 2>/dev/null || true
  fi
  exit "$status"
}

request_shutdown() {
  SHUTDOWN_REQUESTED=true
  exit 0
}

trap cleanup EXIT
trap request_shutdown INT TERM HUP

emit preflight running ',"label":"Checking this Mac"'
if [[ ! -f "$ROOT_FILE" ]]; then
  fail_phase preflight missing-component
fi
IFS= read -r REPO_ROOT < "$ROOT_FILE" || true
if [[ -z "${REPO_ROOT:-}" || ! -x "$REPO_ROOT/scripts/launch.sh" ]]; then
  fail_phase preflight missing-component
fi
for command_name in uv npm curl; do
  command -v "$command_name" >/dev/null 2>&1 || fail_phase preflight missing-component
done
for port in 3000 8000 8001; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail_phase preflight port-conflict
  fi
done
mkdir -p "$LOG_DIR" || fail_phase preflight invalid-state-path
emit preflight complete

emit resources running ',"label":"Using the development checkout"'
emit resources complete ',"skipped":true'
emit sandbox running ',"label":"Using the development sandbox"'
emit sandbox complete ',"skipped":true'

# Electron owns stdin. Closing the app closes the pipe, which asks this wrapper
# to trigger the same cleanup path as Cmd-Q.
MAIN_PID=$$
exec 9<&0
(
  while IFS= read -r _; do :; done
  kill -TERM "$MAIN_PID" 2>/dev/null || true
) <&9 &
EOF_PID=$!
exec 9<&-

emit mcp running ',"label":"Starting scientific tools"'
(
  export VISTA_MCP_DISABLE_SERVERS="${VISTA_MCP_DISABLE_SERVERS:-submit_job}"
  exec "$REPO_ROOT/scripts/launch.sh" logs --no-build --no-electron
) >> "$STACK_LOG" 2>&1 &
STACK_PID=$!

START_TIMEOUT="${VISTA_DEV_START_TIMEOUT:-180}"
wait_for_url() {
  local url="$1" elapsed=0
  while (( elapsed < START_TIMEOUT )); do
    kill -0 "$STACK_PID" 2>/dev/null || return 1
    if curl -sS --max-time 2 -o /dev/null "$url" 2>/dev/null; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}

wait_for_url 'http://127.0.0.1:8000/mcp' || fail_phase mcp health-timeout dev-stack.log
emit mcp ready
emit backend running ',"label":"Preparing VISTA"'
wait_for_url 'http://127.0.0.1:8001/openapi.json' || fail_phase backend health-timeout dev-stack.log
emit backend ready
emit ui running ',"label":"Starting the interface"'
wait_for_url 'http://localhost:3000' || fail_phase ui health-timeout dev-stack.log
emit ui ready ',"url":"http://localhost:3000"'

# A service failure after readiness returns control to Electron, which restores
# the preparation window and points the developer at dev-stack.log.
wait "$STACK_PID"
