#!/bin/bash -l
# Frontier Slurm script for the example job.
# Runs inside the IRI JobSpec's bash -l -c invocation, with VISTA_OUT and
# RUN_DIR_Frontier pre-set by vista's IRI dispatcher (submit_job_mcp.py).
#
# Note: no inner srun here because this example is a 1-rank job (node_count=1,
# no processes_per_node in cluster_defaults.json -> Vista sets process_count=1).
# For multi-rank / distributed jobs on Frontier, you DO need an inner srun —
# Frontier's IRI service launches the batch script once on the head node and
# leaves task fan-out to the user (mirrors Odo). See hpc_jobs/forge-tune/job.frontier.slurm
# for an 8-ranks-per-node example.
#
# Everything it creates, the virtual environment included, goes under $VISTA_OUT.
# The job runs as the project's IRI automation user, which can only read the
# source folder VISTA uploaded, so this is what lets it run from any project.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"

cd "${RUN_DIR_Frontier}"

module load cray-python

python3 -m venv "$VISTA_OUT/.venv"
source "$VISTA_OUT/.venv/bin/activate"
python3 -m pip install --quiet matplotlib

python example.py "$@"
