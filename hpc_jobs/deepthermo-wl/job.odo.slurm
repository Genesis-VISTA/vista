#!/bin/bash -l
# Odo (OLCF open enclave) Slurm script for deepthermo-wl via the IRI API.
#
# Runs inside vista's IRI `bash -l -c` invocation, which has already exported VISTA_OUT
# and mkdir'd it, exported the OLCF HTTP proxy, set HOME, and run `module purge`.
# The stage order ("$@") is the script_args forwarded verbatim — see README.md.
#
# GPU MPI job: one rank per GCD (8 GCDs/node on Frontier), each rank one PT replica.
#
# LIBTORCH IS NOT BUILT HERE. Unlike alloy-thermo-mc (plain MPI, builds in ~2 min), this
# engine links LibTorch, which must be pre-provisioned on the cluster and pointed at by
# DEEPTHERMO_TORCH_PREFIX. The engine itself IS built per job (CMake + C++, a few
# minutes) unless DEEPTHERMO_ENGINE_BIN names a prebuilt hea-wl.
#
# WORKSPACE PERSISTS. The three stages (collect -> train -> sample) are separate jobs,
# so snapshots and the exported model must outlive each one. The workspace is a named
# directory on shared storage that is NOT deleted on exit, and NOT inside $VISTA_OUT —
# get_hpc_job_status recursively lists the output dir over Globus, and a directory of
# snapshots would make a status check crawl. Only results.json and small diagnostics
# go to $VISTA_OUT.
#
# Env vars from the dispatcher + cluster_defaults.json:
#   VISTA_OUT                 per-job output dir, already mkdir'd.
#   RUN_DIR_Odo               where vista Globus-staged run_stage.py.
#   DEEPTHERMO_REPO_URL/REF   public DeepThermo-WL repo + branch (default: vae-proposal).
#   XFORGE_ENV                optional override for the xforge env script
#                             (default: /gpfs/wolf2/olcf/gen150/proj-shared/xforge/xforge-env.sh).
#   DEEPTHERMO_TORCH_PREFIX   optional — standalone LibTorch; default derives it from
#                             the xforge module's PyTorch.
#   DEEPTHERMO_ENGINE_BIN     optional prebuilt hea-wl; skips the in-job build.
#   DEEPTHERMO_WS_ROOT        optional override for the persistent workspace root.
#   (the workspace NAME is a script_args flag, not an env var — submit_hpc_job
#    passes no environment, so it has to travel with the order.)

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Odo:?RUN_DIR_Odo not set}"
# LibTorch is derived from the module's PyTorch (torch.utils.cmake_prefix_path) —
# the same thing the repo's generic build instruction does. Override only if you have a
# standalone LibTorch you would rather link against.

REPO_URL="${DEEPTHERMO_REPO_URL:-https://github.com/jqyin/DeepThermo-WL}"
REPO_REF="${DEEPTHERMO_REPO_REF:-vae-proposal}"
JOBID="${SLURM_JOB_ID:-$$}"
NRANKS="${SLURM_NTASKS:-$(( ${SLURM_NNODES:-1} * 8 ))}"

# Persistent, shared, and outside $VISTA_OUT — survives between stages.
WS_ROOT="${DEEPTHERMO_WS_ROOT:-$(dirname "${VISTA_OUT}")/deepthermo-ws}"
mkdir -p "${WS_ROOT}"

# Ephemeral: the clone and the build. Shared (every rank execve's the binary), removed on exit.
SHARED="$(dirname "${VISTA_OUT}")/deepthermo-build-${JOBID}"
mkdir -p "${SHARED}"
trap 'rm -rf "${SHARED}"' EXIT
CLONE_DIR="${SHARED}/DeepThermo-WL"

# Same environment as the forge-tune job. On Odo xforge is a shell script to source,
# not a module (see hpc_jobs/forge-tune/job.odo.slurm) — it brings PyTorch + ROCm.
source "${XFORGE_ENV:-/gpfs/wolf2/olcf/gen150/proj-shared/xforge/xforge-env.sh}"
module load PrgEnv-gnu cmake 2>/dev/null || true
TORCH_PREFIX="${DEEPTHERMO_TORCH_PREFIX:-$("${DEEPTHERMO_PYTHON:-python3}" -c 'import torch,sys; print(torch.utils.cmake_prefix_path)' 2>/dev/null)}"
[ -n "${TORCH_PREFIX}" ] || { echo "[deepthermo-wl] could not locate LibTorch via torch.utils.cmake_prefix_path" >&2; exit 1; }
TORCH_LIB="$("${DEEPTHERMO_PYTHON:-python3}" -c 'import torch,os; print(os.path.join(os.path.dirname(torch.__file__),"lib"))')"
export LD_LIBRARY_PATH="${TORCH_LIB}:${LD_LIBRARY_PATH:-}"

# 1. Clone WITH the submodule — vae-modeling carries the TorchScript export used to
#    bootstrap a random-weight model, without which collect mode cannot start.
echo "[deepthermo-wl] cloning ${REPO_URL}@${REPO_REF}"
git clone --depth 1 --branch "${REPO_REF}" --recurse-submodules --shallow-submodules \
    "${REPO_URL}" "${CLONE_DIR}"

# 2. Engine: prebuilt if provided, else build against the provisioned LibTorch.
if [ -n "${DEEPTHERMO_ENGINE_BIN:-}" ] && [ -x "${DEEPTHERMO_ENGINE_BIN}" ]; then
    ENGINE_BIN="${DEEPTHERMO_ENGINE_BIN}"
    echo "[deepthermo-wl] using prebuilt engine ${ENGINE_BIN}"
else
    # Odo is MI250X/ROCm like Frontier, so "frontier" is the matching platform target
    # (CMake accepts generic|frontier|perlmutter only).
    echo "[deepthermo-wl] building hea-wl against ${TORCH_PREFIX}"
    cmake -B "${CLONE_DIR}/build" -S "${CLONE_DIR}" \
        -DCMAKE_PREFIX_PATH="${TORCH_PREFIX}" \
        -DCMAKE_CXX_COMPILER=CC \
        -DDEEPTHERMO_BACKEND=torch \
        -DDEEPTHERMO_PLATFORM=frontier
    cmake --build "${CLONE_DIR}/build" --parallel 8
    ENGINE_BIN="${CLONE_DIR}/build/hea-wl"
fi
test -x "${ENGINE_BIN}" || { echo "[deepthermo-wl] no hea-wl produced" >&2; exit 1; }

# 2b. The binary must be visible from every node, or srun fails with execve() partway in.
if [ "${SLURM_NNODES:-1}" -gt 1 ]; then
    srun --ntasks="${SLURM_NNODES}" --ntasks-per-node=1 test -x "${ENGINE_BIN}" || {
        echo "[deepthermo-wl] ${ENGINE_BIN} not visible on all nodes — it must be on shared storage." >&2
        exit 1; }
fi

PYTHON="${DEEPTHERMO_PYTHON:-$(command -v python3)}"
"${PYTHON}" -c "import torch" 2>/dev/null || {
    echo "[deepthermo-wl] ${PYTHON} has no torch — did XFORGE_ENV source cleanly?" >&2; exit 1; }

echo "[deepthermo-wl] ready on $(hostname): ranks=${NRANKS} ws_root=${WS_ROOT}"

# 3. Run one stage. The wrapper runs serially and launches srun itself, because the
#    rank count is the PT ladder size.
HOME="${SHARED}" "${PYTHON}" -u "${RUN_DIR_Odo}/run_stage.py" \
    --skill-root "${CLONE_DIR}" \
    --engine-bin "${ENGINE_BIN}" \
    --output-dir "${VISTA_OUT}" \
    --workspace-root "${WS_ROOT}" \
    --python "${PYTHON}" \
    --ranks "${NRANKS}" \
    --launcher srun \
    "$@"
