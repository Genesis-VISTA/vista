#!/bin/bash -l
# Frontier Slurm script for the example job.
# Runs inside the IRI JobSpec's bash -l -c invocation, with VISTA_OUT and
# RUN_DIR_Frontier pre-set by vista's IRI dispatcher (submit_job_mcp.py).

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"

cd "${RUN_DIR_Frontier}"

module load cray-python

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --quiet matplotlib

srun python example.py "$@"
