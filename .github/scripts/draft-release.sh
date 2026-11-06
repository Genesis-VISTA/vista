#!/usr/bin/env bash
# Create or update the draft GitHub release for a tag, and attach its packages,
# its one-line installers, and its update.json and vista-schema.sqlite.
#
#   .github/scripts/draft-release.sh <dir with the archives, .sha256 files, installers and data-compat.sh output> <notes.md>
#
# Takes TAG and VERSION from the environment, and GH_TOKEN for `gh`. A tag
# with a prerelease suffix (v0.2.0-rc1) makes a prerelease. Run again for the
# same tag, it replaces the assets and the generated notes but keeps whatever a
# maintainer wrote under "What's changed". It refuses a release that has
# already been published.

set -euo pipefail

dir="${1:?usage: draft-release.sh <archives-dir> <notes.md>}"
notes="${2:?usage: draft-release.sh <archives-dir> <notes.md>}"
die() { echo "error: $*" >&2; exit 1; }
[[ -n "${TAG:-}" && -n "${VERSION:-}" ]] || die "TAG and VERSION must be set"

shopt -s nullglob
assets=("$dir"/vista-*.tar.gz "$dir"/vista-*.zip "$dir"/vista-*.sha256)
(( ${#assets[@]} > 0 )) || die "no archives in $dir"
for installer in install.sh install.ps1; do
  [[ -f "$dir/$installer" ]] || die "no $installer in $dir; render it with render-installers.sh"
  assets+=("$dir/$installer")
done
# What the app's update check reads to say whether an update resets local data,
# and the empty database the next release checks itself against.
for record in update.json vista-schema.sqlite; do
  [[ -f "$dir/$record" ]] || die "no $record in $dir; write it with data-compat.sh"
  assets+=("$dir/$record")
done

# GitHub's per-file limit for release assets.
limit=$((2 * 1024 * 1024 * 1024))
for asset in "${assets[@]}"; do
  size="$(wc -c < "$asset" | tr -d ' ')"
  (( size < limit )) || die "$(basename "$asset") is $size bytes, over GitHub's 2 GiB limit for a release asset"
done

prerelease=()
[[ "$VERSION" == *-* ]] && prerelease=(--prerelease)

if gh release view "$TAG" --json isDraft >/dev/null 2>&1; then
  [[ "$(gh release view "$TAG" --json isDraft --jq .isDraft)" == true ]] \
    || die "the release for $TAG is already published; delete it, or tag a new version"
  # Carry the maintainer's own section over into the regenerated notes.
  # A draft edited in the browser comes back with CRLF line endings, which
  # the section match below would not see.
  current="$(mktemp)"
  gh release view "$TAG" --json body --jq .body | tr -d '\r' > "$current"
  merged="$(mktemp)"
  perl -e '
    my ($current, $fresh) = map { local $/; open my $f, "<", $_ or die "$_: $!"; <$f> } @ARGV;
    my $section = qr/(## What.s changed\n).*?(?=\n## )/s;
    if ($current =~ $section) { my $kept = $&; $fresh =~ s/$section/$kept/; }
    print $fresh;
  ' "$current" "$notes" > "$merged"
  gh release edit "$TAG" --title "VISTA $VERSION" --notes-file "$merged" ${prerelease[@]+"${prerelease[@]}"}
  echo "updated the draft for $TAG"
else
  gh release create "$TAG" --draft --verify-tag --title "VISTA $VERSION" \
    --notes-file "$notes" ${prerelease[@]+"${prerelease[@]}"}
  echo "created a draft for $TAG"
fi

gh release upload "$TAG" "${assets[@]}" --clobber
echo "attached: $(for a in "${assets[@]}"; do basename "$a"; done | tr '\n' ' ')"
