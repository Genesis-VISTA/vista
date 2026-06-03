#!/bin/bash -l
# Frontier Slurm script for the example job.
# Runs inside the IRI JobSpec's bash -l -c invocation, with VISTA_OUT and
# RUN_DIR_Frontier pre-set by vista's IRI dispatcher (submit_job_mcp.py).
#
# Note: don't call `srun` here. amscrot's IRI service already launches this
# script once per task in parallel (via process_count from cluster_defaults.json).
# An inner srun would try to create a new step inside the already-allocated one.
# Mirrors the Perlmutter pattern in job.perlmutter.slurm.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"

cd "${RUN_DIR_Frontier}"

module load cray-python

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --quiet matplotlib

python example.py "$@"
