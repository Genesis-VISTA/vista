#!/usr/bin/env bash
# Run Vista CI checks locally (mirrors .gitlab-ci.yml).
#
# Usage:
#   ./scripts/ci-local.sh                  # all targets, lint + test
#   ./scripts/ci-local.sh lint             # lint all targets
#   ./scripts/ci-local.sh test             # test all targets
#   ./scripts/ci-local.sh backend          # lint + test backend
#   ./scripts/ci-local.sh ui lint          # lint UI only
#   ./scripts/ci-local.sh mcp test         # test vista_mcp_server + dev_mcp_server
#   ./scripts/ci-local.sh backend ui test  # test backend + UI (UI has no tests; lint/typecheck only)
#   ./scripts/ci-local.sh install-hooks    # point git at .githooks (lint on commit)
#
# Flags:
#   --fast     Skip advisory/slow lint (pyright, bandit, tsc); used by pre-commit
#   --strict   Fail on advisory jobs that CI marks allow_failure
#   -h, --help Show this help

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FAST=false
STRICT=false
INSTALL_HOOKS=false

TARGETS=()
ACTIONS=()

usage() {
  sed -n '2,18p' "$0" | sed -E 's/^# ?//'
}

die() {
  echo "error: $*" >&2
  exit 1
}

log() {
  printf '\n==> %s\n' "$*"
}

ci_var() {
  # ci_var <NAME> — read a top-level variable's value from .gitlab-ci.yml.
  local name="$1" value
  value="$(sed -nE "s/^[[:space:]]+${name}:[[:space:]]*\"?([^\"#[:space:]]+)\"?.*/\1/p" \
    "$REPO_ROOT/.gitlab-ci.yml" | head -1)"
  [[ -n "$value" ]] || die "could not read $name from .gitlab-ci.yml"
  printf '%s' "$value"
}

# Pin lint tooling to the same versions CI uses, read from .gitlab-ci.yml so
# there is a single source of truth. Without this, `uvx` resolves the latest
# release locally and a pipeline that is green here goes red in CI (ruff 0.16
# reformats code that 0.15 accepts).
RUFF_VERSION="$(ci_var RUFF_VERSION)"
PYRIGHT_VERSION="$(ci_var PYRIGHT_VERSION)"
BANDIT_VERSION="$(ci_var BANDIT_VERSION)"
export RUFF_VERSION PYRIGHT_VERSION BANDIT_VERSION
# Steps run as child processes (see `step`), so anything they reference has to
# be exported rather than merely set.
export REPO_ROOT FAST

FAILED=0

# Run one step of a section, as its own `bash -c` with its own errexit, and
# record the result here rather than relying on it to propagate.
#
# Both halves of that matter. Bash disables `set -e` inside a function invoked
# as the condition of `if` — which is how `run_section` calls every section —
# and that suppression is inherited by ordinary `( ... )` subshells too. So a
# failing step used to be stepped over, and because a section's exit status is
# that of its *last* command, one passing step afterwards made the whole
# section report green. That masked a required job for real: a stale uv.lock
# left `vista-mcp:test` unable to import boto3, the step never ran, and this
# script still printed "All requested checks passed" and exited 0.
#
# A child process gets a fresh errexit context, which also means a multi-command
# step stops at its first failure instead of running on and returning the status
# of the last command.
step() {
  local label="$1" script="$2"
  log "$label"
  if bash -c "set -euo pipefail
$script"; then
    echo "ok: $label"
    return 0
  fi
  echo "fail: $label" >&2
  FAILED=1
}

# A step mirroring a CI job with `allow_failure: true`: warns instead of
# failing the run unless --strict.
advisory_step() {
  local label="$1" script="$2"
  log "$label"
  if bash -c "set -euo pipefail
$script"; then
    echo "ok: $label"
    return 0
  fi
  if [[ "$STRICT" != true ]]; then
    echo "warn: $label failed (advisory; pass --strict to fail)" >&2
    return 0
  fi
  echo "fail: $label" >&2
  FAILED=1
}

ensure_uv() {
  command -v uv >/dev/null 2>&1 || die "uv is required (https://docs.astral.sh/uv/)"
}

ensure_npm() {
  command -v npm >/dev/null 2>&1 || die "npm is required"
}

backend_lint() {
  ensure_uv
  step "backend:lint (ruff $RUFF_VERSION)" '
    cd "$REPO_ROOT/backend"
    uvx "ruff@${RUFF_VERSION}" check src/ tests/
    uvx "ruff@${RUFF_VERSION}" format --check src/ tests/
  '
  if [[ "$FAST" == true ]]; then
    return 0
  fi
  # Match CI: typecheck is a required gate (the pyright baseline is clean);
  # security is advisory (allow_failure).
  step "backend:typecheck" '
    cd "$REPO_ROOT/backend"
    uv sync --frozen
    uv run --with "pyright==${PYRIGHT_VERSION}" pyright src/
  '
  advisory_step "backend:security" '
    cd "$REPO_ROOT/backend"
    uvx "bandit@${BANDIT_VERSION}" -r src/ -ll -q
  '
}

PYTEST_HERMETIC_MARKERS='not live and not hpc and not sandbox'
export PYTEST_HERMETIC_MARKERS

backend_test() {
  ensure_uv
  step "backend:test" '
    cd "$REPO_ROOT/backend"
    uv sync --frozen --dev
    uv run pytest tests/ -v --tb=short -m "$PYTEST_HERMETIC_MARKERS"
  '
}

mcp_lint() {
  ensure_uv
  step "vista-mcp:lint (ruff tests/)" '
    cd "$REPO_ROOT/mcp_servers/vista_mcp_server"
    uvx "ruff@${RUFF_VERSION}" check tests/
    uvx "ruff@${RUFF_VERSION}" format --check tests/
  '
  step "dev-mcp:lint (ruff)" '
    cd "$REPO_ROOT/mcp_servers/dev_mcp_server"
    uvx "ruff@${RUFF_VERSION}" check src/ tests/
    uvx "ruff@${RUFF_VERSION}" format --check src/ tests/
  '
}

mcp_test() {
  ensure_uv
  # Required (matches GitLab vista-mcp:test)
  step "vista-mcp:test" '
    cd "$REPO_ROOT/mcp_servers/vista_mcp_server"
    uv sync --frozen --extra dev
    uv run pytest tests/ -v --tb=short -m "$PYTEST_HERMETIC_MARKERS"
  '
  # Required: container-dependent tests are marked `sandbox` and excluded here.
  step "dev-mcp:test" '
    cd "$REPO_ROOT/mcp_servers/dev_mcp_server"
    uv sync --frozen --extra dev
    uv run pytest tests/ -v --tb=short -m "$PYTEST_HERMETIC_MARKERS"
  '
}

ui_lint() {
  ensure_npm
  step "ui:lint" '
    cd "$REPO_ROOT/ui"
    if [[ "$FAST" == true ]]; then
      if [[ ! -d node_modules ]]; then
        npm ci --prefer-offline
      fi
    else
      npm ci --prefer-offline
    fi
    npm run lint
  '
  if [[ "$FAST" != true ]]; then
    step "ui:typecheck" '
      cd "$REPO_ROOT/ui"
      npx tsc --noEmit
    '
  fi
}

ui_test() {
  # UI has no unit-test job in CI; typecheck is covered under lint.
  echo "note: UI has no pytest/jest job in CI; use './scripts/ci-local.sh ui lint' for ESLint + tsc"
}

install_hooks() {
  git -C "$REPO_ROOT" config core.hooksPath .githooks
  chmod +x "$REPO_ROOT/.githooks/pre-commit" "$REPO_ROOT/scripts/ci-local.sh"
  echo "Installed git hooks (core.hooksPath=.githooks)."
  echo "Pre-commit will run: ./scripts/ci-local.sh lint --fast"
}

# ─── parse args ─────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --fast)
      FAST=true
      ;;
    --strict)
      STRICT=true
      ;;
    install-hooks)
      INSTALL_HOOKS=true
      ;;
    backend|ui|mcp|all)
      TARGETS+=("$1")
      ;;
    lint|test|tests)
      ACTIONS+=("$1")
      ;;
    *)
      die "unknown argument: $1 (try --help)"
      ;;
  esac
  shift
done

if [[ "$INSTALL_HOOKS" == true ]]; then
  install_hooks
  exit 0
fi

# Normalize actions: tests → test
NORMALIZED_ACTIONS=()
for a in "${ACTIONS[@]+"${ACTIONS[@]}"}"; do
  case "$a" in
    tests) NORMALIZED_ACTIONS+=("test") ;;
    *) NORMALIZED_ACTIONS+=("$a") ;;
  esac
done
ACTIONS=("${NORMALIZED_ACTIONS[@]+"${NORMALIZED_ACTIONS[@]}"}")

# Defaults: all targets, all actions
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=(all)
fi
if [[ ${#ACTIONS[@]} -eq 0 ]]; then
  ACTIONS=(lint test)
fi

want_target() {
  local t="$1"
  local x
  for x in "${TARGETS[@]}"; do
    [[ "$x" == "all" || "$x" == "$t" ]] && return 0
  done
  return 1
}

want_action() {
  local a="$1"
  local x
  for x in "${ACTIONS[@]}"; do
    [[ "$x" == "$a" ]] && return 0
  done
  return 1
}

run_section() {
  local label="$1"
  shift
  # Individual steps record their own failures (see `step`); this only catches a
  # section that fails some other way.
  if ! "$@"; then
    echo "fail: $label" >&2
    FAILED=1
  fi
}

if want_target backend && want_action lint; then
  run_section "backend lint" backend_lint
fi
if want_target backend && want_action test; then
  run_section "backend test" backend_test
fi
if want_target mcp && want_action lint; then
  run_section "mcp lint" mcp_lint
fi
if want_target mcp && want_action test; then
  run_section "mcp test" mcp_test
fi
if want_target ui && want_action lint; then
  run_section "ui lint" ui_lint
fi
if want_target ui && want_action test; then
  ui_test
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo ""
  echo "One or more checks failed."
  exit 1
fi

echo ""
echo "All requested checks passed."
