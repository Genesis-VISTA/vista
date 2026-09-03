#!/bin/bash -l
# Odo pre_launch validation for salt-neutronics-tbr. Runs ONCE on the head compute node
# before job.odo.slurm (inlined into the IRI JobSpec pre_launch by vista's dispatcher).
# Fails fast — before the allocation does any work — if its prerequisite is missing.
#
# Env provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   RUN_DIR_Odo  where vista materialized run_state_point.py (inlined into the JobSpec).
#
# There is no pre-provisioned env to check (job.odo.slurm builds a throwaway venv at
# runtime), so the only prerequisite is that the wrapper was staged.

set -euo pipefail

: "${RUN_DIR_Odo:?RUN_DIR_Odo not set}"

echo "[setup_odo] RUN_DIR_Odo=${RUN_DIR_Odo}"

if [ ! -f "${RUN_DIR_Odo}/run_state_point.py" ]; then
    echo "[setup_odo] ERROR: run_state_point.py missing in ${RUN_DIR_Odo}" >&2
    echo "[setup_odo] vista inlines this file into the JobSpec pre_launch; check the job log for base64/mkdir errors." >&2
    exit 1
fi

echo "[setup_odo] OK: run_state_point.py present"
