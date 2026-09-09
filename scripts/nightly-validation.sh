#!/usr/bin/env bash
# Nightly / manual validation lane (Milestone D).
#
# Wraps the evaluation runbook's agent-mode golden prompts, dry-run HPC, and
# fault-injection checks. NEVER required for MR merges — secrets belong only in
# scheduled pipeline variables (or a local env).
#
# Usage (stack already running with dry-run HPC + a real model):
#   export VISTA_BACKEND_MODEL=anthropic:claude-sonnet-4-6
#   export VISTA_RUN_LIVE=1
#   export VISTA_MCP_HPC_DRY_RUN=true   # on the MCP process
#   ./scripts/nightly-validation.sh
#
# Optional:
#   VISTA_LIVE_BASE_URL=http://127.0.0.1:8001
#   VISTA_RUN_PLAYWRIGHT=1            # UI smoke (schedule/manual only)
#   VISTA_RUN_FAULT_CHECKS=1         # fault-injection tools-mode pass
#   VISTA_RUN_HPC=1                   # weekly real HPC (not default nightly)
#
# See docs/validation-lane.md for ownership, flake policy, and schedule setup.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BASE_URL="${VISTA_LIVE_BASE_URL:-http://127.0.0.1:8001}"
export VISTA_LIVE_BASE_URL="$BASE_URL"
export VISTA_RUN_LIVE="${VISTA_RUN_LIVE:-1}"

log() { printf '\n==> %s\n' "$*"; }
fail() { echo "error: $*" >&2; exit 1; }

[[ "${VISTA_BACKEND_MODEL:-}" != "test" && -n "${VISTA_BACKEND_MODEL:-}" ]] \
  || fail "VISTA_BACKEND_MODEL must be a real provider (got '${VISTA_BACKEND_MODEL:-}')"

log "1/4 Golden agent-mode prompts (soft tool allowlists)"
(
  cd backend
  uv sync --frozen --dev
  uv run pytest tests/live/test_golden_prompts.py -v --tb=short -m live
)

log "2/4 Dry-run HPC path via loadgen (tools mode, no LLM)"
(
  cd backend
  uv run python scripts/loadgen.py --campaigns 2 --concurrency 1 --poll
)

if [[ "${VISTA_RUN_FAULT_CHECKS:-0}" == "1" ]]; then
  log "3/4 Fault-injection tools-mode check (runbook note)"
  echo "Ensure MCP was started with e.g. VISTA_MCP_FAULT__SUBMIT_FAIL_P=0.2"
  echo "and VISTA_MCP_HPC_DRY_RUN=true; then re-run loadgen --mode agent for recovery."
  (
    cd backend
    uv run python scripts/loadgen.py --campaigns 4 --concurrency 1 --poll || true
  )
else
  log "3/4 Fault checks skipped (set VISTA_RUN_FAULT_CHECKS=1 to enable)"
fi

if [[ "${VISTA_RUN_PLAYWRIGHT:-0}" == "1" ]]; then
  log "4/4 Playwright UI smoke (schedule/manual only)"
  (
    cd ui
    npx --yes playwright@1.51.0 install --with-deps chromium
    npx --yes playwright@1.51.0 test -c playwright.config.ts
  )
else
  log "4/4 Playwright skipped (set VISTA_RUN_PLAYWRIGHT=1 to enable)"
fi

if [[ "${VISTA_RUN_HPC:-0}" == "1" ]]; then
  log "Optional: real HPC weekly smoke"
  (
    cd backend
    uv run pytest tests/live/test_hpc_example_smoke.py -v --tb=short -m hpc
  )
fi

log "Validation lane finished (failures here must not block MR merges)"
