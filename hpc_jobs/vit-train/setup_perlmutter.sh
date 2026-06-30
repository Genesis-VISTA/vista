#!/bin/bash -l
# Perlmutter pre_launch validation for vit-train. Runs ONCE on the compute node before
# job.perlmutter.slurm (inlined into the IRI JobSpec pre_launch by vista's dispatcher).
# Fails fast — before the allocation does any work — if its prerequisites are missing.
#
# Env provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   RUN_DIR_Perlmutter    where vista staged run_state_point.py via the IRI Filesystem API.
#   CLIMATEVIT_DATA_ROOT  ERA5 dataset root on a NERSC filesystem (required to train here).

set -euo pipefail

: "${RUN_DIR_Perlmutter:?RUN_DIR_Perlmutter not set}"

echo "[setup_perlmutter] RUN_DIR_Perlmutter=${RUN_DIR_Perlmutter}"

if [ ! -f "${RUN_DIR_Perlmutter}/run_state_point.py" ]; then
    echo "[setup_perlmutter] ERROR: run_state_point.py missing in ${RUN_DIR_Perlmutter}" >&2
    echo "[setup_perlmutter] vista should have uploaded it via the IRI Filesystem API;" >&2
    echo "[setup_perlmutter] check VISTA_MCP_NERSC_REMOTE_DIR + IRI token validity." >&2
    exit 1
fi

if [ -z "${CLIMATEVIT_DATA_ROOT:-}" ]; then
    echo "[setup_perlmutter] WARNING: CLIMATEVIT_DATA_ROOT not set. The default ERA5 path is on"
    echo "[setup_perlmutter] OLCF and won't exist on NERSC — stage ERA5 and set CLIMATEVIT_DATA_ROOT"
    echo "[setup_perlmutter] in cluster_defaults.json iri.environment, or the training will fail." >&2
fi

echo "[setup_perlmutter] OK: run_state_point.py present"
