#!/bin/bash -l
# Frontier Slurm script for salt-chemistry-md via the OLCF moderate-enclave IRI API.
#
# Runs inside vista's IRI `bash -l -c` invocation, which has already exported the
# OLCF HTTP proxy, set HOME to a writable dir, and done `module purge`. One node,
# one rank (node_count=1, no processes_per_node) -> we use a single inner srun to
# bind one GCD for the GPU MD run (mirrors the repo's templates/submit_frontier.sbatch).
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   VISTA_OUT          per-job output dir, already mkdir'd. results.json lands here.
#   RUN_DIR_Frontier   <frontier_remote_dir>/<job>/src — where vista staged run_state_point.py.
#   SALTMD_ENV         prefix of the pre-provisioned OpenMM+MACE ROCm conda env (REQUIRED).
#   SALTMD_REPO_URL    public salt-chemistry-skill repo to clone (the saltmd code + Flibe model).
#   SALTMD_REPO_REF    branch/tag/SHA to check out (default: main).
#   http(s)_proxy      OLCF HTTP proxy (compute nodes have no direct outbound) — set by vista.
#
# The state-point order ("$@") is forwarded verbatim to run_state_point.py — see README.md.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"
: "${SALTMD_ENV:?SALTMD_ENV not set; add it to cluster_defaults.json iri.environment}"

REPO_URL="${SALTMD_REPO_URL:-https://github.com/jqyin/salt-chemistry-skill}"
REPO_REF="${SALTMD_REPO_REF:-main}"
CLONE_DIR="${VISTA_OUT}/salt-chemistry-skill"

# 1. Clone the simulation code at runtime (system git; proxy already exported by vista).
echo "[salt-chemistry-md] cloning ${REPO_URL}@${REPO_REF} -> ${CLONE_DIR}"
rm -rf "${CLONE_DIR}"
git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}"
if [ "${REPO_REF}" != "main" ]; then
    git -C "${CLONE_DIR}" fetch --depth 1 origin "${REPO_REF}"
    git -C "${CLONE_DIR}" checkout --detach FETCH_HEAD
fi

# 2. Activate the pre-provisioned OpenMM + MACE ROCm conda env. Module versions match
#    the repo's templates/frontier_env.sh / install_frontier_rocm_stack.sh; override via env.
module load "${PRGENV_MODULE:-PrgEnv-gnu/8.7.0}"
module load "${CPE_MODULE:-cpe/26.03}"
module load "${MINIFORGE_MODULE:-miniforge3/23.11.0-0}"
module load "${ROCM_MODULE:-rocm/7.1.1}"
module load "${ACCEL_MODULE:-craype-accel-amd-gfx90a}"
export LD_LIBRARY_PATH="${CRAY_LD_LIBRARY_PATH:-}:${LD_LIBRARY_PATH:-}"

eval "$(conda shell.bash hook)"
conda activate "${SALTMD_ENV}"

# OpenMM backend for the AMD MI250X GPUs (run_npt.py reads these from the env).
export OPENMM_PLATFORM="${OPENMM_PLATFORM:-HIP}"
export OPENMM_PRECISION="${OPENMM_PRECISION:-mixed}"
export OPENMM_DEVICE_INDEX="${OPENMM_DEVICE_INDEX:-0}"

echo "[salt-chemistry-md] env ready on $(hostname): python=$(which python) OPENMM_PLATFORM=${OPENMM_PLATFORM}"

# 3. Build the structure + run NPT on one GPU. run_state_point.py turns the flat order
#    into build_structure.py + run_npt.py against the cloned repo, writing results.json.
srun -N1 -n1 -c7 --gpus-per-task=1 --gpu-bind=closest \
    python -u "${RUN_DIR_Frontier}/run_state_point.py" \
        --skill-root "${CLONE_DIR}" \
        --output-dir "${VISTA_OUT}" \
        "$@"
