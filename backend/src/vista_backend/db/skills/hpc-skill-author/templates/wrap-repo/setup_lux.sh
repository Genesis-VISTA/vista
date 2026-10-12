#!/bin/bash
# Runs on the Lux LOGIN node before every sbatch (Lux compute nodes cannot
# clone). Checks out REPO_URL at REPO_COMMIT, both from cluster_defaults.json,
# into $VISTA_JOB_DIR/src/<commit>: the first submission clones, later ones
# find it there. A new commit gets its own folder, so a run never sees a
# half-updated tree.
set -euo pipefail
: "${VISTA_JOB_DIR:?VISTA_JOB_DIR not set}"
: "${REPO_URL:?REPO_URL not set (cluster_defaults.json lux.iri.environment)}"
: "${REPO_COMMIT:?REPO_COMMIT not set (cluster_defaults.json lux.iri.environment)}"

SRC="$VISTA_JOB_DIR/src/$REPO_COMMIT"
mkdir -p "$VISTA_JOB_DIR/src"
exec 9>"$VISTA_JOB_DIR/src/.lock"
flock 9

if [ ! -d "$SRC/.git" ]; then
  rm -rf "$SRC.partial"
  git clone --quiet "$REPO_URL" "$SRC.partial"
  git -C "$SRC.partial" checkout --quiet --detach "$REPO_COMMIT"
  # TODO: one-time build steps go here, run in "$SRC.partial", e.g.
  # (cd "$SRC.partial" && python3 -m venv .venv && .venv/bin/pip install -q -e .)
  mv "$SRC.partial" "$SRC"
fi
echo "source: $REPO_URL @ $(git -C "$SRC" rev-parse HEAD) in $SRC"
