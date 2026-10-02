#!/usr/bin/env bash
# Work out what a release run builds, before any runner is spent on it.
#
#   RELEASE_PLATFORMS='["linux-x86","win-x86"]' .github/scripts/plan.sh
#
# Reads the repository variable RELEASE_PLATFORMS (a JSON list of platform
# keys; unset or empty means all three) and the build-inputs pin, and writes to
# $GITHUB_OUTPUT, or to stdout when that is unset:
#
#   package        the package job's matrix include list, one entry per platform
#   images         the sandbox-image architectures those platforms need
#   inputs_commit  the pinned Genesis-VISTA/vista-build-inputs commit
#   narrowed       true when RELEASE_PLATFORMS named fewer than all platforms
#
# The narrowed set exists for a private rehearsal only (design D12): the
# release job refuses it on a public repository.

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"

die() { echo "error: $*" >&2; exit 1; }

# Every platform a release builds. `verify` is how its package job verifies:
# hosted macOS runners cannot boot the sandbox (design D4, D10).
ALL='[
  {"platform":"linux-x86","runner":"ubuntu-24.04","arch":"amd64","verify":"full"},
  {"platform":"mac-arm64","runner":"macos-15","arch":"arm64","verify":"without-sandbox"},
  {"platform":"win-x86","runner":"windows-2025","arch":"amd64","verify":"full"}
]'

requested="${RELEASE_PLATFORMS:-}"
if [[ -z "$requested" ]]; then
  requested="$(jq -c '[.[].platform]' <<<"$ALL")"
fi
jq -e 'type == "array" and length > 0 and all(type == "string")' <<<"$requested" >/dev/null 2>&1 \
  || die "RELEASE_PLATFORMS must be a non-empty JSON list of platform keys, got: $requested"
unknown="$(jq -rn --argjson all "$ALL" --argjson req "$requested" \
  '$req - [$all[].platform] | join(", ")')"
[[ -z "$unknown" ]] || die "RELEASE_PLATFORMS names unknown platforms: $unknown \
(known: $(jq -r '[.[].platform] | join(", ")' <<<"$ALL"))"

package="$(jq -cn --argjson all "$ALL" --argjson req "$requested" \
  '[$all[] | select(.platform as $p | $req | index($p))]')"
images="$(jq -c '[.[].arch] | unique' <<<"$package")"
narrowed=false
[[ "$(jq length <<<"$package")" -lt "$(jq length <<<"$ALL")" ]] && narrowed=true

# The pin. A placeholder fails here, before any build starts.
pin_file="$repo/.github/build-inputs.env"
[[ -f "$pin_file" ]] || die "no $pin_file"
inputs_commit="$(sed -nE 's/^BUILD_INPUTS_COMMIT=([^[:space:]#]*).*/\1/p' "$pin_file" | head -1)"
[[ "$inputs_commit" =~ ^[0-9a-f]{40}$ ]] \
  || die "no build inputs pinned: BUILD_INPUTS_COMMIT in .github/build-inputs.env is \
'$inputs_commit', not a full commit hash. See docs/releasing.md."

out="${GITHUB_OUTPUT:-/dev/stdout}"
{
  echo "package=$package"
  echo "images=$images"
  echo "inputs_commit=$inputs_commit"
  echo "narrowed=$narrowed"
} >> "$out"
