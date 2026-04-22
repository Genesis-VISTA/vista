#!/bin/bash -l
#SBATCH -J example
#SBATCH -t 0:02:00
#SBATCH -A stf218
#SBATCH -N 1

source "$VISTA_SETUP_SCRIPT"

module load cray-python
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install matplotlib

srun python example.py "$@"
