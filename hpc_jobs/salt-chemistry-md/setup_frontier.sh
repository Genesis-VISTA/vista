#!/bin/bash -l
# Frontier pre_launch validation for salt-chemistry-md. Runs ONCE on the head compute
# node before job.frontier.slurm. Inlined into the IRI JobSpec by vista's dispatcher.
# Fails fast (before the GPU allocation does any work) if its prerequisites are missing.
#
# Env provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   SALTMD_ENV        prefix of the pre-provisioned OpenMM+MACE conda env (must already exist).
#   RUN_DIR_Frontier  where vista staged run_state_point.py.

set -euo pipefail

: "${SALTMD_ENV:?SALTMD_ENV not set; add it to cluster_defaults.json iri.environment}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"

echo "[setup_frontier] SALTMD_ENV=${SALTMD_ENV}"
echo "[setup_frontier] RUN_DIR_Frontier=${RUN_DIR_Frontier}"

if [ ! -d "${SALTMD_ENV}" ]; then
    echo "[setup_frontier] ERROR: pre-provisioned conda env not found at ${SALTMD_ENV}" >&2
    echo "[setup_frontier] Create it once at that prefix with the repo's install_frontier_rocm_stack.sh" >&2
    echo "[setup_frontier] (OpenMM[hip] + openmm-ml + torch + mace-torch). See hpc_jobs/salt-chemistry-md/README.md." >&2
    exit 1
fi

if [ ! -f "${RUN_DIR_Frontier}/run_state_point.py" ]; then
    echo "[setup_frontier] ERROR: run_state_point.py missing in ${RUN_DIR_Frontier}" >&2
    echo "[setup_frontier] vista should have staged it via Globus; verify the upload + dir permissions." >&2
    exit 1
fi

echo "[setup_frontier] OK: conda env prefix + run_state_point.py present"
