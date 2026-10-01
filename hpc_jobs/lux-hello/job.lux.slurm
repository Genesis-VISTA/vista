#!/bin/bash -l
# Lux Slurm body for lux-hello. Vista's Lux dispatcher prepends the #SBATCH
# header (one node, walltime, log paths; no account, so Slurm charges your
# default) and exports VISTA_OUT, an already-created per-job output directory.
# Nothing is installed or cloned.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"

{
  echo "args: $*"
  echo "host: $(hostname)"
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "slurm job: ${SLURM_JOB_ID:-unknown} on ${SLURM_JOB_NODELIST:-unknown}"
  if command -v rocm-smi >/dev/null 2>&1; then
    rocm-smi --showproductname || echo "rocm-smi failed (exit $?)"
  else
    echo "rocm-smi not found"
  fi
} | tee "${VISTA_OUT}/hello.txt"
