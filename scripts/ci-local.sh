#!/usr/bin/env bash
# Run Vista CI checks locally (mirrors .gitlab-ci.yml).
#
# Usage:
#   ./scripts/ci-local.sh                  # all targets, lint + test
#   ./scripts/ci-local.sh lint             # lint all targets
#   ./scripts/ci-local.sh test             # test all targets
#   ./scripts/ci-local.sh backend          # lint + test backend
#   ./scripts/ci-local.sh ui lint          # lint UI only
#   ./scripts/ci-local.sh mcp test         # test dev_mcp_server only
#   ./scripts/ci-local.sh chart lint       # helm lint + template (dev + prod paths)
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
  sed -n '2,19p' "$0" | sed -E 's/^# ?//'
}

die() {
  echo "error: $*" >&2
  exit 1
}

log() {
  printf '\n==> %s\n' "$*"
}

run_job() {
  # run_job <name> <allow_failure 0|1> <command...>
  local name="$1"
  local allow_failure="$2"
  shift 2
  log "$name"
  if "$@"; then
    echo "ok: $name"
    return 0
  fi
  local rc=$?
  if [[ "$allow_failure" -eq 1 && "$STRICT" != true ]]; then
    echo "warn: $name failed (advisory; pass --strict to fail)" >&2
    return 0
  fi
  echo "fail: $name (exit $rc)" >&2
  return "$rc"
}

ensure_uv() {
  command -v uv >/dev/null 2>&1 || die "uv is required (https://docs.astral.sh/uv/)"
}

ensure_npm() {
  command -v npm >/dev/null 2>&1 || die "npm is required"
}

ensure_helm() {
  command -v helm >/dev/null 2>&1 || die "helm is required (https://helm.sh/docs/intro/install/)"
}

backend_lint() {
  ensure_uv
  log "backend:lint (ruff)"
  (
    cd "$REPO_ROOT/backend"
    uvx ruff check src/ tests/
    uvx ruff format --check src/ tests/
  )
  if [[ "$FAST" == true ]]; then
    return 0
  fi
  # Match CI: typecheck + security are advisory (allow_failure)
  run_job "backend:typecheck" 1 bash -c '
    cd "'"$REPO_ROOT"'/backend"
    uv sync --frozen
    uv run --with pyright pyright src/
  '
  run_job "backend:security" 1 bash -c '
    cd "'"$REPO_ROOT"'/backend"
    uvx bandit -r src/ -ll -q
  '
}

backend_test() {
  ensure_uv
  log "backend:test"
  (
    cd "$REPO_ROOT/backend"
    uv sync --frozen --dev
    uv run pytest tests/ -v --tb=short
  )
}

mcp_lint() {
  ensure_uv
  log "dev-mcp:lint (ruff)"
  (
    cd "$REPO_ROOT/mcp_servers/dev_mcp_server"
    uvx ruff check src/ tests/
    uvx ruff format --check src/ tests/
  )
}

mcp_test() {
  ensure_uv
  # Match CI: allow_failure unless --strict (microsandbox may be unavailable)
  run_job "dev-mcp:test" 1 bash -c '
    cd "'"$REPO_ROOT"'/mcp_servers/dev_mcp_server"
    uv sync --frozen --extra dev
    uv run pytest tests/ -v --tb=short
  '
}

ui_lint() {
  ensure_npm
  log "ui:lint"
  (
    cd "$REPO_ROOT/ui"
    if [[ "$FAST" == true ]]; then
      if [[ ! -d node_modules ]]; then
        npm ci --prefer-offline
      fi
      npm run lint
    else
      npm ci --prefer-offline
      npm run lint
      log "ui:typecheck"
      npx tsc --noEmit
    fi
  )
}

ui_test() {
  # UI has no unit-test job in CI; typecheck is covered under lint.
  echo "note: UI has no pytest/jest job in CI; use './scripts/ci-local.sh ui lint' for ESLint + tsc"
}

chart_lint() {
  ensure_helm
  log "chart:lint (helm lint + template)"
  helm lint chart/
  # Dev bootstrap: inline secrets + KVM privileged mode
  helm template vista chart/ \
    --set appSecrets.create=true \
    --set appSecrets.openaiApiKey=test \
    --set kvm.enabled=true \
    > /dev/null
  # Production path: ExternalSecrets + KVM disabled
  helm template vista chart/ \
    --set appSecrets.create=false \
    --set appSecrets.externalSecret.enabled=true \
    --set appSecrets.externalSecret.secretsManagerPath=/amsc/dev/vista \
    --set oidc.externalSecret.enabled=true \
    --set oidc.externalSecret.secretsManagerPath=/amsc/dev/vista-oidc \
    --set kvm.enabled=false \
    > /dev/null
}

chart_test() {
  echo "note: chart has no test job in CI; use './scripts/ci-local.sh chart lint' for helm lint + template"
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
    backend|ui|mcp|chart|all)
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

FAILED=0

run_section() {
  local label="$1"
  shift
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
if want_target chart && want_action lint; then
  run_section "chart lint" chart_lint
fi
if want_target chart && want_action test; then
  chart_test
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo ""
  echo "One or more checks failed."
  exit 1
fi

echo ""
echo "All requested checks passed."
