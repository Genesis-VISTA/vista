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
# would be testing itself.
LAUNCHER=("$PACKAGE/vista")
if [[ "$IS_WINDOWS" == true ]]; then
  [[ -f "$PACKAGE/vista.ps1" ]] || die "no launcher at $PACKAGE/vista.ps1"
  LAUNCHER=(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PACKAGE/vista.ps1")
else
  [[ -x "$PACKAGE/vista" ]] || die "no launcher at $PACKAGE/vista"
fi

export VISTA_HOME="$STATE"

# There is no opt-out for hardware virtualisation: VISTA always needs a
# microVM, so a host where the launcher refuses to start for want of one
# cannot verify a package, and this test fails there rather than skipping the
# checks that go through the sandbox.
#
# This test deliberately configures no Globus credential, which is now the
# ordinary state of a fresh install: one arrives when a researcher connects
# Globus in the interface. Unset rather than assumed absent, so a maintainer
# with tokens exported in their own shell tests the same thing the build does.
unset VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN \
      VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN \
      VISTA_MCP_FRONTIER_GLOBUS_HTTPS_REFRESH_TOKEN

export VISTA_UI_PORT="$UI_PORT"
export VISTA_MCP_PORT="$MCP_PORT"
export VISTA_BACKEND_PORT="$BACKEND_PORT"
BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"

mkdir -p "$STATE" "$LOGS"

log "starting the package launcher"
"${LAUNCHER[@]}" > "$LOGS/launcher.log" 2>&1 &
PIDS+=($!)

# The launcher prints one address line when every service is up.
for (( i = 0; i < 600; i++ )); do
  grep -q 'VISTA is running at' "$LOGS/launcher.log" 2>/dev/null && break
  if ! kill -0 "${PIDS[0]}" 2>/dev/null; then
    tail -20 "$LOGS/launcher.log" >&2
    die "the launcher exited before reporting an address"
  fi
  perl -e 'select(undef, undef, undef, 1)'
done
grep -q 'VISTA is running at' "$LOGS/launcher.log" \
  || { tail -20 "$LOGS/launcher.log" >&2; die "the launcher never reported an address"; }

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

# Retrieval, through the backend's MCP client -- the same path the agent uses,
# so it covers the store, the embedding weights loading offline, and the
# knowledge base having actually been registered.
retrieval_returns_passages() {
  local body
  body="$(
    curl -s -m 120 -X POST \
      "$BACKEND_URL/projects/molten-salt/mcp/call" \
      -H 'content-type: application/json' \
      -d '{"name":"rag_search","arguments":{"query":"thermal conductivity of molten fluoride salts","kb_slug":"molten-salt-papers"}}'
  )"
  echo "$body" > "$LOGS/retrieval.json"
  "$PACKAGE_PYTHON" - "$LOGS/retrieval.json" <<'PYCHECK'
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
check "retrieval returns passages" retrieval_returns_passages

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
  local declared reported
  declared="$(cat "$PACKAGE/VERSION")"
  reported="$(
    curl -s -m 20 "$BACKEND_URL/openapi.json" \
      | "$PACKAGE_PYTHON" -c \
        'import json,sys; print(json.load(sys.stdin.buffer)["info"]["version"])'
  )"
  [[ -n "$declared" && "$declared" == "$reported" ]] \
    && grep -q "VISTA $declared" "$LOGS/launcher.log"
}
check "version matches across manifest, launcher and app" version_is_consistent

log "shutting down"
cleanup

if (( FAILED )); then
  echo >&2
  echo "error: smoke test failed; logs are in $LOGS" >&2
  exit 1
fi
echo "all checks passed"
