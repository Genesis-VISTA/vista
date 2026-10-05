#!/usr/bin/env bash
# Render the release notes from .github/release-notes.md.
#
#   .github/scripts/render-notes.sh <dir with the archives and .sha256 files>
#
# Takes from the environment:
#
#   VERSION        the release version, e.g. 0.2.0-rc1
#   TAG            the tag, e.g. v0.2.0-rc1
#   COMMIT         the VISTA commit the packages were built from
#   INPUTS_COMMIT  the build-inputs commit they were built with
#   REPO_URL       e.g. https://github.com/Genesis-VISTA/vista
#   PACKAGE_PLAN   the package matrix from plan.sh: which platforms were built,
#                  and how each was verified
#
# Writes the notes to stdout. Fails when a planned platform has no archive.

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dir="${1:?usage: render-notes.sh <archives-dir>}"

die() { echo "error: $*" >&2; exit 1; }
for var in VERSION TAG COMMIT INPUTS_COMMIT REPO_URL PACKAGE_PLAN; do
  [[ -n "${!var:-}" ]] || die "$var is not set"
done

table=''
checklist=''
while IFS=$'\t' read -r platform verify; do
  sha_file="$(find "$dir" -maxdepth 1 -name "vista-$VERSION-$platform.*.sha256" | head -1)"
  [[ -n "$sha_file" ]] || die "no archive for $platform in $dir"
  archive="$(basename "${sha_file%.sha256}")"
  sha="$(cut -d' ' -f1 < "$sha_file")"
  table+="| $platform | \`$archive\` | \`$sha\` |"$'\n'
  if [[ "$verify" == without-sandbox ]]; then
    checklist+="- [ ] **$platform**: CI could not run the sandbox, so it verified this package \
without it and retrieval went unchecked. Run the full smoke test on a real machine against \
this draft's archive before publishing: unpack it, then run \
\`scripts/smoke_test_package.sh <unpacked folder>\` from a VISTA checkout."$'\n'
  else
    checklist+="- [x] **$platform**: verified in CI with the sandbox, retrieval included."$'\n'
  fi
done < <(jq -r '.[] | [.platform, .verify] | @tsv' <<<"$PACKAGE_PLAN")

ARCHIVE_TABLE="${table%$'\n'}" \
CHECKLIST="${checklist%$'\n'}" \
DOWNLOAD_URL="$REPO_URL/releases/download/$TAG" \
  perl -pe 's/\$\{(VERSION|TAG|COMMIT|INPUTS_COMMIT|REPO_URL|DOWNLOAD_URL|ARCHIVE_TABLE|CHECKLIST)\}/$ENV{$1}/g' \
  "$here/../release-notes.md"
