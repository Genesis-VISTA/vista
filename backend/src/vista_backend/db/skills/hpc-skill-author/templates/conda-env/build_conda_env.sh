#!/bin/bash
# Build a conda environment once, for every run of this job, and leave it at
# $ENV_PREFIX. Call it from setup_lux.sh (Lux login node) or from the job
# script (Frontier, Odo) with:
#   ENV_PARENT  folder that holds the env, e.g. "$VISTA_JOB_DIR/envs" (Lustre)
#   ENV_NAME    the env's name, e.g. "melt-py311"; change it to force a rebuild
#   ENV_SPEC    an environment.yml, e.g. "$RUN_DIR_Lux/environment.yml"
#               (or leave it unset and edit the `conda create` line below)
# and then activate it with: conda activate "$ENV_PARENT/$ENV_NAME"
#
# Follows hpc-skill-author's "Software environment" steps: proxy first, keep
# the package cache and pip builds off Lustre, stripe count 1 for the env.
set -euo pipefail
: "${ENV_PARENT:?set ENV_PARENT, e.g. \$VISTA_JOB_DIR/envs}"
: "${ENV_NAME:?set ENV_NAME}"
ENV_PREFIX="$ENV_PARENT/$ENV_NAME"

# 1. Network access goes through the OLCF proxy.
export https_proxy="${https_proxy:-http://proxy.ccs.ornl.gov:3128}"
export http_proxy="${http_proxy:-http://proxy.ccs.ornl.gov:3128}"

# 2. TODO: load what the cluster's docs say to build with (https://docs.olcf.ornl.gov/),
#    e.g. on Frontier:  module load miniforge3 PrgEnv-gnu cray-mpich
command -v conda >/dev/null || { echo "ERROR: conda not found; load the cluster's conda module first" >&2; exit 3; }

mkdir -p "$ENV_PARENT"
exec 8>"$ENV_PARENT/.$ENV_NAME.lock"
flock 8
if [ -f "$ENV_PREFIX/.vista-env-ok" ]; then
  echo "env: $ENV_PREFIX (already built)"
  exit 0
fi

# 3. Lustre is slow for an env's many small files: build caches on local disk,
#    and one stripe for the env's folder.
export CONDA_PKGS_DIRS="/tmp/$USER/conda_pkgs"
export TMPDIR="/tmp/$USER/tmp"
mkdir -p "$CONDA_PKGS_DIRS" "$TMPDIR"
if command -v lfs >/dev/null; then
  lfs setstripe -c 1 "$ENV_PARENT" || echo "warning: lfs setstripe failed; continuing" >&2
fi

rm -rf "$ENV_PREFIX"
if [ -n "${ENV_SPEC:-}" ]; then
  conda env create --prefix "$ENV_PREFIX" --file "$ENV_SPEC"
else
  # TODO: the packages the job needs.
  conda create --yes --prefix "$ENV_PREFIX" python=3.11
fi
# TODO: pip packages that build against the cluster's MPI, e.g.
# MPICC="cc -shared" "$ENV_PREFIX/bin/pip" install --no-cache-dir --no-binary=mpi4py mpi4py

conda env export --prefix "$ENV_PREFIX" > "$ENV_PREFIX/environment.lock.yml"
touch "$ENV_PREFIX/.vista-env-ok"
echo "env: $ENV_PREFIX (built)"
