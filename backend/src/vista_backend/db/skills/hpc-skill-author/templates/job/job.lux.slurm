#!/bin/bash -l
# __SKILL__/__JOB__ on Lux. VISTA writes the #SBATCH header from
# cluster_defaults.json and exports:
#   VISTA_OUT      this run's output folder (already created); write outputs here
#   RUN_DIR_Lux    this job folder's uploaded files; read only
#   VISTA_JOB_DIR  state shared by every run of this job (set up by setup_lux.sh)
# Args ("$@", VISTA's script_args): KEY=VALUE pairs, see README.md.
set -euo pipefail
: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Lux:?RUN_DIR_Lux not set}"

# Defaults, then the allow-list: anything not listed fails the job at once.
STEPS=10
for kv in "$@"; do
  case "$kv" in
    STEPS=*) declare "$kv" ;;
    *) echo "ERROR: unknown argument '$kv' (accepted: STEPS=)" >&2; exit 2 ;;
  esac
done

cd "$VISTA_OUT"
{
  echo "host: $(hostname)"
  echo "steps: $STEPS"
  # TODO: the real work, e.g.
  # srun python "$RUN_DIR_Lux/run.py" --steps "$STEPS" --out "$VISTA_OUT"
} 2>&1 | tee "$VISTA_OUT/run.log"

tail -n 5 "$VISTA_OUT/run.log" > "$VISTA_OUT/summary.txt"
