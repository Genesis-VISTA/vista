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

cleanup() {
  trap - INT TERM EXIT
  local pid
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill "$pid" 2>/dev/null || true
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

# ─── environment ────────────────────────────────────────────────────────────

# Nothing about the package may depend on where it was built or where it is now,
# so every path is derived from its own location. `VISTA_HPC_JOBS_DIR` and
# `VISTA_BUILD_RAG_DIR` exist because the two paths they replace are derived by
# walking up from a module's file, which lands inside the virtual environment
# once the project is installed non-editably.
export PATH="$PACKAGE/bin:$PACKAGE/node/bin:$PATH"
export UV_NO_SYNC=1
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
# whole point of shipping the weights is that this works offline.
export HF_HUB_OFFLINE=1
export MSB_HOME="$STATE/microsandbox"

# Use the sandbox image already imported into msb, instead of building it.
#
# `dev_mcp_server` defaults `dockerfile` to the Dockerfile inside its own
# package, and the microsandbox backend treats a dockerfile as "build this with
# docker or podman first" -- so on a host with neither, the very first agent
# session dies with `docker or podman not found on PATH`. Clearing it takes the
# other branch, which asks msb whether the image is present and needs no
# container runtime at all. This is what the no-Docker promise actually rests
# on, alongside shipping the image.
export VISTA_DEV_MCP_DOCKERFILE=""
export VISTA_DEV_MCP_IMAGE="vista-sandbox:latest"

# ─── first-run setup ────────────────────────────────────────────────────────

log "preparing state at $STATE"
mkdir -p "$STATE" "$LOGS"

# The payload is copied rather than read in place: the knowledge-base row
# records absolute paths, and the corpus is the researcher's to add to.
for part in vista-data knowledge-bases huggingface; do
  if [[ -d "$PACKAGE/payload/$part" && ! -e "$STATE/$part" ]]; then
    cp -R "$PACKAGE/payload/$part" "$STATE/$part"
  fi
done

MSB="$(find "$PACKAGE/app/mcp_servers/dev_mcp_server/.venv" \
  -path '*/microsandbox/_bundled/bin/msb' | head -1)"
[[ -x "$MSB" ]] || die "no msb binary in $PACKAGE"
if [[ -f "$PACKAGE/payload/sandbox-image.tar" ]]; then
  if ! MSB_HOME="$MSB_HOME" "$MSB" image inspect --format=json vista-sandbox:latest \
       >/dev/null 2>&1; then
    log "importing the sandbox image"
    MSB_HOME="$MSB_HOME" "$MSB" load -i "$PACKAGE/payload/sandbox-image.tar" \
      -t vista-sandbox:latest
  fi
fi

# ─── services ───────────────────────────────────────────────────────────────

log "starting services"
"$PACKAGE/app/mcp_servers/vista_mcp_server/.venv/bin/vista-mcp-server" \
  --transport=http --port "$MCP_PORT" > "$LOGS/mcp.log" 2>&1 &
PIDS+=($!)
wait_for "$VISTA_MCP_URL" 180 || { tail -20 "$LOGS/mcp.log" >&2; die "MCP server did not start"; }

"$PACKAGE/app/backend/.venv/bin/vista-backend" > "$LOGS/backend.log" 2>&1 &
PIDS+=($!)
wait_for "$VISTA_BACKEND_URL/openapi.json" 300 \
  || { tail -20 "$LOGS/backend.log" >&2; die "backend did not start"; }

PORT="$UI_PORT" HOSTNAME=127.0.0.1 "$PACKAGE/node/bin/node" \
  "$PACKAGE/app/ui/server.js" > "$LOGS/ui.log" 2>&1 &
PIDS+=($!)
wait_for "http://127.0.0.1:$UI_PORT/" 120 \
  || { tail -20 "$LOGS/ui.log" >&2; die "UI did not start"; }

# ─── checks ─────────────────────────────────────────────────────────────────

log "checking"
http_ok() {
  local code
  code="$(curl -s -o /dev/null -m 20 -w '%{http_code}' "$1")"
  [[ "$code" == "200" ]]
}

check "mcp server responds"      curl -s -o /dev/null -m 10 "$VISTA_MCP_URL"
check "backend serves openapi"   http_ok "$VISTA_BACKEND_URL/openapi.json"
check "ui serves its home page"  http_ok "http://127.0.0.1:$UI_PORT/"
check "ui reaches the backend"   http_ok "http://127.0.0.1:$UI_PORT/api/projects"

# Retrieval, through the backend's MCP client -- the same path the agent uses,
# so it covers the store, the embedding weights loading offline, and the
# knowledge base having actually been registered.
retrieval_returns_passages() {
  local body
  body="$(
    curl -s -m 120 -X POST \
      "$VISTA_BACKEND_URL/projects/molten-salt/mcp/call" \
      -H 'content-type: application/json' \
      -d '{"name":"rag_search","arguments":{"query":"thermal conductivity of molten fluoride salts","kb_slug":"molten-salt-papers"}}'
  )"
  echo "$body" > "$LOGS/retrieval.json"
  "$PACKAGE/app/backend/.venv/bin/python" - "$LOGS/retrieval.json" <<'PYCHECK'
import json
import sys

result = json.load(open(sys.argv[1]))
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

log "shutting down"
cleanup

if (( FAILED )); then
  echo >&2
  echo "error: smoke test failed; logs are in $LOGS" >&2
  exit 1
fi
echo "all checks passed"
