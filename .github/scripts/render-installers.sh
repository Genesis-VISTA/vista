#!/usr/bin/env bash
# Write a release's copies of the one-line installers, scripts/install.sh and
# scripts/install.ps1, with its version and repository written in, so that
# releases/latest/download/install.sh installs that release without asking
# GitHub which one is newest.
#
#   .github/scripts/render-installers.sh <out-dir>
#
# Takes VERSION (e.g. 0.2.0-rc1) and REPO_URL (e.g.
# https://github.com/Genesis-VISTA/vista) from the environment.

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="${1:?usage: render-installers.sh <out-dir>}"
die() { echo "error: $*" >&2; exit 1; }
[[ -n "${VERSION:-}" && -n "${REPO_URL:-}" ]] || die "VERSION and REPO_URL must be set"

mkdir -p "$out"
for script in install.sh install.ps1; do
  perl -pe 's/\@VISTA_VERSION\@/$ENV{VERSION}/g; s/\@VISTA_REPO_URL\@/$ENV{REPO_URL}/g' \
    "$here/../../scripts/$script" > "$out/$script"
  ! grep -q '@VISTA_' "$out/$script" || die "$script still has a placeholder"
done
chmod +x "$out/install.sh"
echo "installers: $out/install.sh, $out/install.ps1 (VISTA $VERSION)"
