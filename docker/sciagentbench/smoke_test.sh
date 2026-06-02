#!/usr/bin/env bash
# Boot the two active SciAgentBench substrate containers from compose and
# run a minimal liveness check on each. Satisfies the Phase-9 acceptance
# criterion: "Both substrate containers boot from compose and pass a
# smoke test." (The Redis memory container is deferred to CHUNKS.)
#
# Usage:
#   docker/sciagentbench/smoke_test.sh           # boot, test, tear down
#   docker/sciagentbench/smoke_test.sh --keep    # leave the stack running
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=("docker" "compose" "-f" "$HERE/compose.yml")
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }

cleanup() {
  if [[ "$KEEP" -eq 0 ]]; then
    echo "Tearing down..."
    "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || fail "docker not installed"

echo "[1/3] Booting substrate stack..."
"${COMPOSE[@]}" up -d

# Give services a chance to pass their healthchecks.
echo "[2/3] Waiting for healthchecks (up to ~120s)..."
deadline=$((SECONDS + 120))
wait_healthy() {
  local name="$1"
  while (( SECONDS < deadline )); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo missing)"
    [[ "$status" == "healthy" ]] && return 0
    [[ "$status" == "missing" ]] && return 1
    sleep 3
  done
  return 1
}

echo "[3/3] Smoke-checking each substrate..."

# --- PyPI mirror (pypiserver) ---
wait_healthy sciagentbench-pypi-mirror || fail "pypi mirror never became healthy"
docker exec sciagentbench-pypi-mirror wget -q -O - http://localhost:8080/simple/ >/dev/null \
  || fail "pypi simple index unreachable"
pass "pypi mirror: /simple/ index served"

# --- SLURM cluster ---
wait_healthy sciagentbench-slurmctld || fail "slurmctld never became healthy"
docker exec sciagentbench-slurmctld sinfo >/dev/null \
  || fail "sinfo failed on the controller"
pass "slurm: controller responds to sinfo"

echo "Both substrates booted and passed smoke checks."
echo "SMOKE PASS"
