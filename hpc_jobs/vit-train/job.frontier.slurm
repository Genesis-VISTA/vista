#!/bin/bash -l
# Frontier Slurm script for vit-train via the OLCF moderate-enclave IRI API.
#
# Runs inside vista's IRI `bash -l -c` invocation, which has already exported the OLCF HTTP
# proxy and done `module purge`. We clone climate-vit, load the GPU software stack, then run
# run_state_point.py ON THE HEAD NODE (no outer srun): it renders the ViT config from the
# candidate and launches `srun train_mp.py` itself across the allocation's GPUs (one rank per
# GPU), so all the orchestration stays in one place.
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   VISTA_OUT           per-job output dir, already mkdir'd. results.json + the run dir land here.
#   RUN_DIR_Frontier    <frontier_remote_dir>/<job>/src — where vista staged run_state_point.py.
#   CLIMATEVIT_REPO_URL public climate-vit repo to clone (the train_mp.py code + config).
#   CLIMATEVIT_REPO_REF branch/tag/SHA to check out (default: main).
#   CLIMATEVIT_DATA_ROOT  ERA5 dataset root (default: OLCF world-shared EAR5).
#   CLIMATEVIT_NUM_ITERS  training iterations per candidate (screening budget; default 2000).
#   GPUS_PER_NODE       GPUs per node for geometry (default 8 on Frontier).
#   http(s)_proxy       OLCF HTTP proxy (compute nodes have no direct outbound) — set by vista.
#
# The candidate ("$@") — one JSON object — is forwarded verbatim to run_state_point.py.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"

export HOME="${VISTA_OUT}"
export PYTHONNOUSERSITE=1
export GPUS_PER_NODE="${GPUS_PER_NODE:-8}"

REPO_URL="${CLIMATEVIT_REPO_URL:-https://github.com/jqyin/climate-vit}"
REPO_REF="${CLIMATEVIT_REPO_REF:-main}"
CLONE_DIR="${VISTA_OUT}/climate-vit"

# 1. Clone the training code at runtime (system git; proxy already exported by vista).
echo "[vit-train] cloning ${REPO_URL}@${REPO_REF} -> ${CLONE_DIR}"
rm -rf "${CLONE_DIR}"
git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}"
if [ "${REPO_REF}" != "main" ]; then
    git -C "${CLONE_DIR}" fetch --depth 1 origin "${REPO_REF}"
    git -C "${CLONE_DIR}" checkout --detach FETCH_HEAD
fi

# 2. Load the GPU software stack. climate-vit's own submit scripts use the `xforge` module
#    (PyTorch + ROCm + the parallel deps); override the modulefile path / module name via env,
#    or point CLIMATEVIT_ENV at a conda env prefix to activate instead.
if [ -n "${CLIMATEVIT_ENV:-}" ]; then
    eval "$(conda shell.bash hook)"
    conda activate "${CLIMATEVIT_ENV}"
else
    module use "${CLIMATEVIT_MODULEPATH:-/sw/aaims/crusher/modulefiles}"
    module load "${CLIMATEVIT_MODULE:-xforge}"
fi

# ROCm/RCCL networking for multi-GPU (matches climate-vit's submit_frontier_cp.sh).
export NCCL_NET_GDR_LEVEL=3
export NCCL_CROSS_NIC=1
export NCCL_SOCKET_IFNAME=hsn0
export HDF5_USE_FILE_LOCKING=FALSE
export MASTER_ADDR="$(hostname)"

echo "[vit-train] env ready on $(hostname): python=$(command -v python) nodes=${SLURM_NNODES:-1} gpus/node=${GPUS_PER_NODE}"

# 3. Render the config + launch training. run_state_point.py runs on the head node and
#    issues the inner `srun train_mp.py` across the GPUs, then writes results.json.
python -u "${RUN_DIR_Frontier}/run_state_point.py" \
    --skill-root "${CLONE_DIR}" \
    --output-dir "${VISTA_OUT}" \
    "$@"
