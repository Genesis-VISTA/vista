#!/bin/bash -l
# __SKILL__/__JOB__ on Lux: runs __REPO_URL__ at __REPO_COMMIT__, which
# setup_lux.sh checked out into $VISTA_JOB_DIR/src/$REPO_COMMIT.
# VISTA writes the #SBATCH header from cluster_defaults.json and exports
# VISTA_OUT (write outputs here), RUN_DIR_Lux (this folder's files, read only),
# VISTA_JOB_DIR, and REPO_URL / REPO_COMMIT from iri.environment.
# Args ("$@", VISTA's script_args): KEY=VALUE pairs, see README.md.
set -euo pipefail
: "${VISTA_OUT:?VISTA_OUT not set}"
: "${VISTA_JOB_DIR:?VISTA_JOB_DIR not set}"
: "${REPO_COMMIT:?REPO_COMMIT not set}"
SRC="$VISTA_JOB_DIR/src/$REPO_COMMIT"
[ -d "$SRC" ] || { echo "ERROR: no checkout at $SRC; setup_lux.sh should have made it" >&2; exit 3; }

# Defaults, then the allow-list: anything not listed fails the job at once.
STEPS=10
for kv in "$@"; do
  case "$kv" in
    STEPS=*) declare "$kv" ;;
    *) echo "ERROR: unknown argument '$kv' (accepted: STEPS=)" >&2; exit 2 ;;
  esac
done

# Run from the output folder, so anything the code writes to its working
# directory lands where VISTA can fetch it.
cd "$VISTA_OUT"
{
  echo "source: ${REPO_URL:-?} @ $REPO_COMMIT"
  echo "host: $(hostname)"
  # TODO: the real command, using the repository's own entry point, e.g.
  # srun python "$SRC/train.py" --steps "$STEPS" --out "$VISTA_OUT"
  echo "steps: $STEPS"
} 2>&1 | tee "$VISTA_OUT/run.log"

tail -n 5 "$VISTA_OUT/run.log" > "$VISTA_OUT/summary.txt"
