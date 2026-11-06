#!/usr/bin/env bash
# Decide whether updating to this release keeps a researcher's data, and write
# the two release assets that record it (OpenSpec change in-app-updates, D7).
#
#   .github/scripts/data-compat.sh <out dir>
#
# Writes into <out dir>:
#
#   vista-schema.sqlite  an empty database as this release's init_db creates
#                        it, so later releases need not rebuild this tree
#   update.json          {"data_compatible_from": "<version>"}: the oldest
#                        earlier release whose database this release opens.
#                        The app warns that an update from anything older
#                        resets local data.
#
# Earlier releases are walked newest first. Each one's own vista-schema.sqlite
# is downloaded, or, for a release from before this step, built from its tag in
# a git worktree. This release's startup check runs against it, and the walk
# stops at the first refusal. Failing to obtain or check any earlier database
# fails the script, so the release is not drafted.
#
# Takes from the environment:
#
#   VERSION       this release's version, e.g. 0.2.5
#   REPO          owner/name, for `gh` (default: GITHUB_REPOSITORY), with
#                 GH_TOKEN
#   RELEASE_TAGS  instead of asking GitHub, these tags (space separated) are
#                 the earlier releases, and each is built from its tag. For a
#                 local run.
#   SCHEMA_DIR    a folder of <tag>.sqlite files, used before anything is
#                 downloaded or built. For a local run.
#
# Needs uv, and git with the release tags fetched.

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
out="${1:?usage: data-compat.sh <out dir>}"

die() { echo "error: $*" >&2; exit 1; }
[[ -n "${VERSION:-}" ]] || die "VERSION is not set"
repo="${REPO:-${GITHUB_REPOSITORY:-}}"
[[ -n "${RELEASE_TAGS+x}" || -n "$repo" ]] || die "set REPO (or GITHUB_REPOSITORY), or RELEASE_TAGS"
mkdir -p "$out"

work="$(mktemp -d)"
worktrees=()
cleanup() {
  for tree in ${worktrees[@]+"${worktrees[@]}"}; do
    git -C "$root" worktree remove --force "$tree" >/dev/null 2>&1 || true
  done
  rm -rf "$work"
}
trap cleanup EXIT

# The backend's step helpers (vista_backend/db/release_schema.py), run from
# the work folder so no .env above the checkout reaches the backend.
release_schema() {
  (cd "$work" && uv run --project "$root/backend" --frozen --quiet \
    python -m vista_backend.db.release_schema "$@")
}

# An empty database as the backend in <tree> creates it, copied to <file>
# through SQLite's backup, so nothing still in its WAL is lost.
build_db() {
  local tree="$1" file="$2" home
  home="$(mktemp -d "$work/home.XXXXXX")"
  (cd "$home" && env -u VISTA_BACKEND_DATABASE_URL VISTA_DATA_DIR="$home" \
    uv run --project "$tree/backend" --frozen --quiet python -c '
import asyncio, sqlite3, sys
from vista_backend.db.db import init_db
asyncio.run(init_db())
source, target = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
source.backup(target)
target.execute("PRAGMA journal_mode=DELETE")  # one file, no -wal or -shm beside it
target.close()
source.close()
' "$home/vista.db" "$file") || return 1
}

# An earlier release's empty database, at <file>.
earlier_db() {
  local tag="$1" file="$2"
  if [[ -n "${SCHEMA_DIR:-}" && -f "$SCHEMA_DIR/$tag.sqlite" ]]; then
    cp "$SCHEMA_DIR/$tag.sqlite" "$file"
    echo "$tag: using $SCHEMA_DIR/$tag.sqlite" >&2
    return
  fi
  if [[ -z "${RELEASE_TAGS+x}" ]] \
    && gh release download "$tag" --repo "$repo" --pattern vista-schema.sqlite \
      --dir "$work/download-$tag" >/dev/null 2>&1; then
    mv "$work/download-$tag/vista-schema.sqlite" "$file"
    echo "$tag: using its published vista-schema.sqlite" >&2
    return
  fi
  echo "$tag: no published vista-schema.sqlite; building one from the tag" >&2
  local tree="$work/src-$tag"
  git -C "$root" rev-parse --verify --quiet "refs/tags/$tag" >/dev/null \
    || die "tag $tag is not in this checkout; fetch the tags (fetch-depth: 0)"
  git -C "$root" worktree add --detach --quiet "$tree" "$tag"
  worktrees+=("$tree")
  build_db "$tree" "$file" || die "could not create $tag's database"
}

echo "building this release's database" >&2
build_db "$root" "$out/vista-schema.sqlite" || die "could not create this release's database"
release_schema check "$out/vista-schema.sqlite" \
  || die "this release's startup check refuses its own database"

if [[ -n "${RELEASE_TAGS+x}" ]]; then
  read -r -a tags <<< "$RELEASE_TAGS"
else
  tags=()
  while IFS= read -r tag; do tags+=("$tag"); done < <(
    gh api "repos/$repo/releases?per_page=100" --paginate \
      --jq '.[] | select(.draft | not) | .tag_name')
fi

listed="$(release_schema older "$VERSION" ${tags[@]+"${tags[@]}"})" \
  || die "could not order the earlier releases"
earlier=()
while IFS= read -r tag; do [[ -n "$tag" ]] && earlier+=("$tag"); done <<< "$listed"
echo "earlier releases, newest first: ${earlier[*]:-none}" >&2

results=()
for tag in ${earlier[@]+"${earlier[@]}"}; do
  db="$work/$tag.sqlite"
  earlier_db "$tag" "$db"
  set +e
  release_schema check "$db"
  status=$?
  set -e
  case "$status" in
    0) echo "$tag: opens without a reset" >&2; results+=("$tag=pass") ;;
    1) echo "$tag: needs a reset (above)" >&2; results+=("$tag=fail"); break ;;
    *) die "the startup check could not run against $tag's database" ;;
  esac
done

release_schema verdict "$VERSION" ${results[@]+"${results[@]}"} > "$out/update.json"
echo "update.json: $(cat "$out/update.json")" >&2
