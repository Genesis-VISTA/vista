#!/bin/bash -l
#SBATCH -J example
#SBATCH -t 0:10:00
#SBATCH -A stf218
#SBATCH -N 1

module load cray-python
srun python example.py "$@"
