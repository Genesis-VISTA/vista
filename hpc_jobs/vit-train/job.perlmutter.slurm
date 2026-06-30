#!/bin/bash -l
# Perlmutter (NERSC) Slurm script for vit-train via the NERSC IRI API.
#
# Secondary backend — Frontier is primary (the default ERA5 dataset lives on OLCF
# world-shared). To train on Perlmutter you must stage ERA5 to a NERSC-visible path and
# set CLIMATEVIT_DATA_ROOT to it. Unlike OLCF, NERSC compute nodes have direct outbound
# network (git clone works without a proxy) and the dispatcher does not cd / module purge.
#
# As with Frontier, run_state_point.py runs on the head node and issues the inner
# `srun train_mp.py` across the node's GPUs itself.
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   VISTA_OUT             per-job output dir, already mkdir'd. results.json + run dir land here.
#   RUN_DIR_Perlmutter    <nersc_remote_dir>/<job>/src — where vista staged run_state_point.py.
#   CLIMATEVIT_REPO_URL   public climate-vit repo to clone (default jqyin/climate-vit).
#   CLIMATEVIT_REPO_REF   branch/tag/SHA to check out (default: main).
#   CLIMATEVIT_DATA_ROOT  ERA5 dataset root on a NERSC filesystem (REQUIRED here).
#   CLIMATEVIT_NUM_ITERS  training iterations per candidate (screening budget; default 2000).
#   GPUS_PER_NODE         GPUs per node for geometry (default 4 on Perlmutter).
#
# The candidate ("$@") — one JSON object — is forwarded verbatim to run_state_point.py.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Perlmutter:?RUN_DIR_Perlmutter not set}"

export HOME="${VISTA_OUT}"
export PYTHONNOUSERSITE=1
export GPUS_PER_NODE="${GPUS_PER_NODE:-4}"

REPO_URL="${CLIMATEVIT_REPO_URL:-https://github.com/jqyin/climate-vit}"
REPO_REF="${CLIMATEVIT_REPO_REF:-main}"
CLONE_DIR="${VISTA_OUT}/climate-vit"

# 1. Clone the training code (NERSC compute nodes reach GitHub directly).
echo "[vit-train] cloning ${REPO_URL}@${REPO_REF} -> ${CLONE_DIR}"
rm -rf "${CLONE_DIR}"
git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}"
if [ "${REPO_REF}" != "main" ]; then
    git -C "${CLONE_DIR}" fetch --depth 1 origin "${REPO_REF}"
    git -C "${CLONE_DIR}" checkout --detach FETCH_HEAD
fi

# 2. GPU software stack. NERSC ships a PyTorch module; override the name via env, or point
#    CLIMATEVIT_ENV at a conda env prefix to activate instead.
if [ -n "${CLIMATEVIT_ENV:-}" ]; then
    eval "$(conda shell.bash hook)"
    conda activate "${CLIMATEVIT_ENV}"
else
    module load "${CLIMATEVIT_MODULE:-pytorch}"
fi

export NCCL_SOCKET_IFNAME=hsn0
export HDF5_USE_FILE_LOCKING=FALSE
export MASTER_ADDR="$(hostname)"

echo "[vit-train] env ready on $(hostname): python=$(command -v python) nodes=${SLURM_NNODES:-1} gpus/node=${GPUS_PER_NODE}"

# 3. Render the config + launch training (run_state_point issues the inner srun train_mp.py).
python -u "${RUN_DIR_Perlmutter}/run_state_point.py" \
    --skill-root "${CLONE_DIR}" \
    --output-dir "${VISTA_OUT}" \
    "$@"
