#!/usr/bin/env bash
# Build and verify one release package: the whole of a release workflow
# `package` job beyond its checkouts, downloads and uploads (design D11), so a
# maintainer can run exactly what CI runs.
#
#   BUILD_INPUTS_DIR=~/vista-build-inputs SANDBOX_IMAGE=sandbox-image-arm64.tar \
#     .github/scripts/package.sh
#
# Configured entirely through the environment:
#
#   BUILD_INPUTS_DIR        a checkout of Genesis-VISTA/vista-build-inputs, holding
#                           vista-data/ and rag_db/ (required)
#   SANDBOX_IMAGE           an exported sandbox image tar for this host's
#                           architecture (required)
#   VISTA_VERSION           the release version; the build's own fallback when unset
#   VERIFY_WITHOUT_SANDBOX  1 to pass --verify-without-sandbox, for a host that
#                           cannot run the sandbox (hosted macOS)
#   AMSC_GIT_TOKEN          a gitlab.com token that can read amscrot-py. Applied
#                           through a temporary global git config for this build
#                           only, so a maintainer's own git credentials are used
#                           when it is unset
#
# Writes `archive=` and `sha256=` to $GITHUB_OUTPUT when that is set.

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

die() { echo "error: $*" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }

[[ -n "${BUILD_INPUTS_DIR:-}" ]] || die "BUILD_INPUTS_DIR is not set"
[[ -d "$BUILD_INPUTS_DIR/vista-data" && -d "$BUILD_INPUTS_DIR/rag_db" ]] \
  || die "BUILD_INPUTS_DIR ($BUILD_INPUTS_DIR) has no vista-data/ and rag_db/"
[[ -n "${SANDBOX_IMAGE:-}" ]] || die "SANDBOX_IMAGE is not set"
[[ -f "$SANDBOX_IMAGE" ]] || die "no sandbox image at $SANDBOX_IMAGE"

case "$(uname -s)" in
  MINGW*|MSYS*) host=windows ;;
  *) host="$(uname -s)" ;;
esac

disk() {
  log "disk free ($1)"
  df -h "$repo" | tail -1
}

disk "before the build"

# What the Windows launcher will check, from the microsandbox version VISTA
# locks, printed up front so a refusal later in the smoke test has its context.
# A diagnostic only: the smoke test is what decides.
if [[ "$host" == windows ]]; then
  log "msb doctor"
  msb_version="$(sed -n '/^name = "microsandbox"$/{n;s/^version = "\(.*\)"$/\1/p;}' \
    "$repo/mcp_servers/dev_mcp_server/uv.lock")"
  uv run --no-project --python 3.14 --with "microsandbox==$msb_version" python -c '
import pathlib, subprocess, sys
import microsandbox
msb = pathlib.Path(microsandbox.__file__).parent / "_bundled" / "bin" / "msb.exe"
sys.exit(subprocess.run([str(msb), "doctor"]).returncode)
' || echo "msb doctor did not report ready (exit $?); the smoke test will say whether that matters"
fi

# The token reaches git through a config file of its own, never the command
# line or the log, and only for this build.
if [[ -n "${AMSC_GIT_TOKEN:-}" ]]; then
  git_config="$(mktemp)"
  trap 'rm -f "$git_config"' EXIT
  git config --file "$git_config" \
    "url.https://oauth2:${AMSC_GIT_TOKEN}@gitlab.com/amsc2/.insteadOf" \
    "https://gitlab.com/amsc2/"
  export GIT_CONFIG_GLOBAL="$git_config"
fi

args=(
  --payload "$BUILD_INPUTS_DIR/vista-data"
  --vector-store "$BUILD_INPUTS_DIR/rag_db"
  --sandbox-image "$SANDBOX_IMAGE"
)
case "${VERIFY_WITHOUT_SANDBOX:-}" in
  1|true) args+=(--verify-without-sandbox) ;;
  ''|0|false) ;;
  *) die "VERIFY_WITHOUT_SANDBOX must be 1 or unset, got: $VERIFY_WITHOUT_SANDBOX" ;;
esac

# The build's last line of output is the archive's path.
build_log="$(mktemp)"
"$repo/scripts/build_local_package.sh" "${args[@]}" | tee "$build_log"
archive="$(tail -1 "$build_log")"
rm -f "$build_log"
[[ -f "$archive" && -f "$archive.sha256" ]] \
  || die "the build did not end with an archive path: $archive"

disk "after the build"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  # Read by actions/upload-artifact, which is not a Git Bash program and cannot
  # resolve /d/a/... paths.
  out_archive="$archive"
  [[ "$host" == windows ]] && out_archive="$(cygpath -w "$archive")"
  {
    echo "archive=$out_archive"
    echo "sha256=$out_archive.sha256"
  } >> "$GITHUB_OUTPUT"
fi
