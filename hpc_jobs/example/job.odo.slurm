#!/bin/bash -l

module load cray-python
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install matplotlib

srun python example.py "$@"
