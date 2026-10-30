#!/usr/bin/env bash
# Builds the pages docs/site.toml publishes the way genesis-vista.github.io
# renders them: syncs this tree into a temporary site made from
# scripts/docs_site/mkdocs.yml, then runs `mkdocs build --strict`, which fails on
# a link or #anchor into another published page that does not resolve. No
# credentials. ci-local.sh's `docs test` and GitLab's docs:site both run this.
#
# Needs MKDOCS_MATERIAL_VERSION (pinned in .gitlab-ci.yml), uv and git.
set -euo pipefail

: "${MKDOCS_MATERIAL_VERSION:?MKDOCS_MATERIAL_VERSION is unset (the pin lives in .gitlab-ci.yml)}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# One pinned environment for PyYAML (the sync), mkdocs and the theme.
run_py() {
  uv run --no-project --quiet --python 3.14 \
    --with "mkdocs-material==${MKDOCS_MATERIAL_VERSION}" python -I "$@"
}

cd "$ROOT"
run_py -m unittest discover -s scripts/docs_site -p 'test_sync_docs.py'

cp scripts/docs_site/mkdocs.yml "$TMP/mkdocs.yml"
run_py scripts/docs_site/sync_docs.py "$ROOT" "$TMP" --ref "$(git -C "$ROOT" describe --tags --always)"

NO_MKDOCS_2_WARNING=1 run_py -m mkdocs build --strict \
  -f "$TMP/mkdocs.generated.yml" --site-dir "$TMP/_site"
