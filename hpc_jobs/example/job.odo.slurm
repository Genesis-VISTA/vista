#!/bin/bash -l
# Odo Slurm script for the example job. VISTA's IRI dispatcher (submit_job_mcp.py)
# sets VISTA_OUT and starts this in the job's source folder (RUN_DIR_Odo).
#
# Everything it creates, the virtual environment included, goes under $VISTA_OUT.
# The job runs as the project's IRI automation user, which can only read the
# source folder VISTA uploaded, so this is what lets it run from any project.

: "${VISTA_OUT:?VISTA_OUT not set}"

module load cray-python
python3 -m venv "$VISTA_OUT/.venv"
source "$VISTA_OUT/.venv/bin/activate"
python3 -m pip install matplotlib

srun python example.py "$@"
