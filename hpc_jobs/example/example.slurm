#!/bin/bash -l
#SBATCH -J example
#SBATCH -t 0:02:00
#SBATCH -A stf218
#SBATCH -N 1

export VISTA_OUT="$VISTA_OUT/$SLURM_JOB_ID"
export https_proxy='http://proxy.ccs.ornl.gov:3128';
export http_proxy='http://proxy.ccs.ornl.gov:3128';
export no_proxy='localhost,127.0.0.1,0.0.0.0';

module load cray-python
python -m venv .venv
source .venv/bin/activate
python -m pip install matplotlib

srun python example.py "$@"
