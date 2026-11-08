#!/bin/bash -l
# __SKILL__/__JOB__ on Odo. VISTA's IRI dispatcher puts this script after a short
# setup (proxy, HOME, `module purge`, `cd` into the job's source folder) and exports:
#   VISTA_OUT      this run's output folder (already created); write outputs here
#   RUN_DIR_Odo    this job folder's uploaded files; read only
# There is no VISTA_JOB_DIR on Odo: put anything a run builds under $VISTA_OUT.
# The job runs as the project's automation user, which can only read RUN_DIR_Odo.
# Args ("$@", VISTA's script_args): KEY=VALUE pairs, see README.md.
set -euo pipefail
: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Odo:?RUN_DIR_Odo not set}"

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
  # module load cray-python
  # srun python "$RUN_DIR_Odo/run.py" --steps "$STEPS" --out "$VISTA_OUT"
} 2>&1 | tee "$VISTA_OUT/run.log"

tail -n 5 "$VISTA_OUT/run.log" > "$VISTA_OUT/summary.txt"
