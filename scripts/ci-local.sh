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
#   ./scripts/ci-local.sh backend ui test  # test backend + UI component suites
#   ./scripts/ci-local.sh electron         # typecheck + routing tests for the window
#   ./scripts/ci-local.sh install          # shellcheck + tests for the one-line installers
#   ./scripts/ci-local.sh launcher         # the package launchers' supervised mode
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

run_job() {
  # run_job <name> <allow_failure 0|1> <command...>
  local name="$1"
  local allow_failure="$2"
  shift 2
  log "$name"
  # Capture the status from the command itself, not from after the `if`.
  #
  # A compound `if` whose condition fails and which has no `else` returns 0, so
  # `$?` afterwards is the *if statement's* status — which is why real failures
  # were reported as "fail: … (exit 0)", a line that reads like a bug in the
  # harness and invites disbelieving the failure.
  local rc=0
  "$@" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    echo "ok: $name"
    return 0
  fi
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

backend_lint() {
  ensure_uv
  local rc=0
  # Gate through run_job so a ruff failure actually fails this script. A bare subshell
  # took the exit status of its LAST command and nothing checked it, so `ruff check`
  # errors were printed and then discarded: the script said "All requested checks
  # passed" and exited 0 while GitLab failed the same commit.
  run_job "backend:lint (ruff $RUFF_VERSION)" 0 bash -c '
    set -e
    cd "'"$REPO_ROOT"'/backend"
    uvx "ruff@${RUFF_VERSION}" check src/ tests/
    uvx "ruff@${RUFF_VERSION}" format --check src/ tests/
  ' || rc=1
  if [[ "$FAST" == true ]]; then
    return "$rc"
  fi
  # Match CI: typecheck is a required gate (the pyright baseline is clean);
  # security is advisory (allow_failure).
  run_job "backend:typecheck" 0 bash -c '
    set -e
    cd "'"$REPO_ROOT"'/backend"
    uv sync --frozen
    uv run --with "pyright==${PYRIGHT_VERSION}" pyright src/
  ' || rc=1
  run_job "backend:security" 1 bash -c '
    set -e
    cd "'"$REPO_ROOT"'/backend"
    uvx "bandit@${BANDIT_VERSION}" -r src/ -ll -q
  '
  return "$rc"
}

PYTEST_HERMETIC_MARKERS='not live and not hpc and not sandbox'
# Surface text I/O that relies on the locale's encoding; the pytest config turns it into
# an error. Matches the test jobs in .gitlab-ci.yml.
export PYTHONWARNDEFAULTENCODING=1

backend_test() {
  ensure_uv
  log "backend:test"
  (
    cd "$REPO_ROOT/backend"
    uv sync --frozen --dev
    uv run pytest tests/ -v --tb=short -m "$PYTEST_HERMETIC_MARKERS"
  )
}

mcp_lint() {
  ensure_uv
  local rc=0
  # See backend_lint: these must gate, not just print.
  run_job "vista-mcp:lint (ruff tests/)" 0 bash -c '
    set -e
    cd "'"$REPO_ROOT"'/mcp_servers/vista_mcp_server"
    uvx "ruff@${RUFF_VERSION}" check tests/
    uvx "ruff@${RUFF_VERSION}" format --check tests/
  ' || rc=1
  run_job "dev-mcp:lint (ruff)" 0 bash -c '
    set -e
    cd "'"$REPO_ROOT"'/mcp_servers/dev_mcp_server"
    uvx "ruff@${RUFF_VERSION}" check src/ tests/
    uvx "ruff@${RUFF_VERSION}" format --check src/ tests/
  ' || rc=1
  return "$rc"
}

mcp_test() {
  ensure_uv
  local rc=0
  # Required (matches GitLab vista-mcp:test)
  log "vista-mcp:test"
  (
    cd "$REPO_ROOT/mcp_servers/vista_mcp_server"
    uv sync --frozen --extra dev
    uv run pytest tests/ -v --tb=short -m "$PYTEST_HERMETIC_MARKERS"
  ) || rc=1
  # Required: container-dependent tests are marked `sandbox` and excluded here.
  log "dev-mcp:test"
  (
    cd "$REPO_ROOT/mcp_servers/dev_mcp_server"
    uv sync --frozen --extra dev
    uv run pytest tests/ -v --tb=short -m "$PYTEST_HERMETIC_MARKERS"
  ) || rc=1
  return "$rc"
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
  ensure_npm
  log "ui:test"
  (
    cd "$REPO_ROOT/ui"
    if [[ "$FAST" == true && -d node_modules ]]; then
      :
    else
      npm ci --prefer-offline
    fi
    # Hermetic: jsdom only, no backend, no MCP server, no model, no database.
    npm run test
    if [[ "$FAST" != true ]]; then
      # Mirrors the required ui:browser job. Needs a Chromium download the
      # first time: npx playwright install chromium
      log "ui:browser"
      npm run test:e2e:hermetic
    fi
  )
}

# The VISTA window (electron/). Mirrors electron:typecheck and electron:test:
# neither needs the Electron binary, so a fresh install skips downloading it.
electron_install() {
  if [[ "$FAST" == true && -d node_modules ]]; then
    return 0
  fi
  if [[ -d node_modules ]]; then
    npm ci --prefer-offline
  else
    ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci --prefer-offline
  fi
}

electron_lint() {
  ensure_npm
  log "electron:typecheck"
  (
    cd "$REPO_ROOT/electron"
    electron_install
    npm run typecheck
  )
}

electron_test() {
  ensure_npm
  log "electron:test"
  (
    cd "$REPO_ROOT/electron"
    electron_install
    npm test
  )
}

# The one-line installers (scripts/install.sh, install.ps1). Mirrors
# install:lint and install:test. shellcheck and pwsh are optional here: each
# part is skipped, and says so, where its tool is missing.
install_lint() {
  if ! command -v shellcheck >/dev/null 2>&1; then
    log "install:lint skipped (no shellcheck; brew install shellcheck)"
    return 0
  fi
  run_job "install:lint (shellcheck)" 0 shellcheck \
    "$REPO_ROOT/scripts/install.sh" "$REPO_ROOT/scripts/test_install.sh" "$REPO_ROOT"/.github/scripts/*.sh
}

install_test() {
  run_job "install:test (install.sh)" 0 "$REPO_ROOT/scripts/test_install.sh"
  if command -v pwsh >/dev/null 2>&1; then
    run_job "install:test (install.ps1)" 0 pwsh -NoProfile -File "$REPO_ROOT/scripts/test_install.ps1"
  else
    log "install:test (install.ps1) skipped (no pwsh; brew install powershell)"
  fi
}

# The package launchers' supervised mode, which the application drives (hermetic:
# fake services, a faked `uname` for each platform). Mirrors launcher:test.
launcher_test() {
  run_job "launcher:test (package_launcher.sh)" 0 bash "$REPO_ROOT/scripts/tests/package_launcher_supervised_test.sh"
  run_job "launcher:test (mac_dev_launcher.sh)" 0 bash "$REPO_ROOT/scripts/tests/mac_dev_launcher_test.sh"
  if command -v pwsh >/dev/null 2>&1; then
    run_job "launcher:test (vista.ps1)" 0 pwsh -NoProfile -File "$REPO_ROOT/scripts/tests/package_launcher_supervised_test.ps1"
  else
    log "launcher:test (vista.ps1) skipped (no pwsh; brew install powershell)"
  fi
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
    backend|ui|mcp|electron|install|launcher|all)
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
  # Run the section in a subshell that re-arms errexit, and capture its status
  # outside any tested context.
  #
  # The obvious `if ! "$@"` is wrong here: bash disables errexit for the whole
  # duration of a function called in a condition, so a lint function whose
  # `ruff check` failed would keep going and return the status of its *last*
  # command instead. That silently passed backend lint errors for as long as
  # this script has existed.
  local status=0
  set +e
  ( set -e; "$@" )
  status=$?
  set -e
  if (( status != 0 )); then
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
if want_target electron && want_action lint; then
  run_section "electron lint" electron_lint
fi
if want_target electron && want_action test; then
  run_section "electron test" electron_test
fi
if want_target install && want_action lint; then
  run_section "install lint" install_lint
fi
if want_target install && want_action test; then
  run_section "install test" install_test
fi
if want_target launcher && want_action test; then
  run_section "launcher test" launcher_test
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo ""
  echo "One or more checks failed."
  exit 1
fi

echo ""
echo "All requested checks passed."
