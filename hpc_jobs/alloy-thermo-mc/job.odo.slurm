#!/bin/bash -l
# Odo (OLCF open enclave) Slurm script for alloy-thermo-mc via the IRI API.
#
# Runs inside vista's IRI `bash -l -c` invocation, which has ALREADY (see the setup
# snippet in submit_job_mcp.py) exported VISTA_OUT and mkdir'd it, exported the OLCF
# HTTP proxy, set HOME, run `module purge`, and `cd`'d into RUN_DIR_Odo (where vista
# Globus-staged run_state_point.py). The composition order ("$@") is the script_args
# forwarded verbatim — see README.md.
#
# This is a CPU-only MPI job: ONE RANK PER TEMPERATURE REPLICA in the parallel-
# tempering ladder. The C++17 engine has no dependencies beyond MPI, so we clone and
# build it at runtime (~1-2 min) rather than pre-provisioning an environment.
#
# WHY THE BUILD GOES ON A SHARED FILESYSTEM (this job spans multiple nodes):
#   srun launches ranks across EVERY node in the allocation, and each rank both
#   execve's the binary and reads control.input / composition.input / coupling.input
#   from the run directory (only rank 0 writes the CSV, but all ranks read). Building
#   into node-local /tmp therefore works on a single node and fails the moment the
#   allocation spans two:
#       error: execve(): /tmp/alloymc-<id>/alloy-thermo-skill/engine/alloy_mc:
#              No such file or directory
#   So the clone, the build, and the run dir live on shared scratch.
#
# WHY THAT SHARED SCRATCH IS *NOT* $VISTA_OUT:
#   `get_hpc_job_status` does a recursive Globus listing of the output dir; a build
#   tree plus a .git dir in there makes a status check hang for minutes. $VISTA_OUT's
#   parent (<odo_remote_dir>.out) is created by Slurm as the job's own user, so a
#   sibling directory there is shared, writable, and out of the listing's path. Only
#   results.json and the small analysis artifacts are written to $VISTA_OUT.
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   VISTA_OUT             per-job output dir, already mkdir'd. results.json lands here.
#   RUN_DIR_Odo           <odo_remote_dir>.<user>.jobs/<job>/src — where vista staged run_state_point.py.
#   ALLOYMC_REPO_URL      public alloy-thermo-skill repo to clone (engine + scripts + DFT spec).
#   ALLOYMC_REPO_REF      branch/tag/SHA to check out (default: main).
#   ALLOYMC_SCRATCH_DIR   optional override for the shared build/run scratch dir.
#   http(s)_proxy         OLCF HTTP proxy (compute nodes have no direct outbound) — set by vista.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Odo:?RUN_DIR_Odo not set}"

REPO_URL="${ALLOYMC_REPO_URL:-https://github.com/jqyin/alloy-thermo-skill}"
REPO_REF="${ALLOYMC_REPO_REF:-main}"
JOBID="${SLURM_JOB_ID:-$$}"

# Ranks == temperature replicas. Prefer what Slurm allocated; fall back to 56/node
# (Odo nodes expose 8 x 7 usable cores, matching the skill's --tasks-per-node 56).
NRANKS="${SLURM_NTASKS:-$(( ${SLURM_NNODES:-1} * 56 ))}"

# Shared scratch — visible to every node. Holds the clone, the build, and the run dir.
SHARED="${ALLOYMC_SCRATCH_DIR:-$(dirname "${VISTA_OUT}")/alloymc-${JOBID}}"
# Node-local scratch — only the batch node needs these. Keeping the Python venv off the
# parallel filesystem avoids a slow pip install eating the walltime budget.
LOCAL="${TMPDIR:-/tmp}/alloymc-${JOBID}"

mkdir -p "${SHARED}" "${LOCAL}"
trap 'rm -rf "${SHARED}" "${LOCAL}"' EXIT
export HOME="${LOCAL}"              # pip/matplotlib drop dotfiles in HOME — keep them off $VISTA_OUT
export XDG_CACHE_HOME="${LOCAL}/.cache"
CLONE_DIR="${SHARED}/alloy-thermo-skill"
VENV="${LOCAL}/venv"

# 1. Clone the engine + scripts at runtime (system git; proxy already exported by vista).
echo "[alloy-thermo-mc] cloning ${REPO_URL}@${REPO_REF} -> ${CLONE_DIR}"
rm -rf "${CLONE_DIR}"
git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}"
if [ "${REPO_REF}" != "main" ]; then
    git -C "${CLONE_DIR}" fetch --depth 1 origin "${REPO_REF}"
    git -C "${CLONE_DIR}" checkout --detach FETCH_HEAD
fi

# 2. Build the MPI engine onto the SHARED filesystem. Cray PE: the C++ MPI wrapper is
#    `CC`, so override the Makefile's mpic++ default. No libraries beyond MPI and C++17.
module load PrgEnv-gnu cray-mpich 2>/dev/null || true
echo "[alloy-thermo-mc] building alloy_mc with CXX=CC"
make -C "${CLONE_DIR}/engine" CXX=CC -j 8
ENGINE_BIN="${CLONE_DIR}/engine/alloy_mc"
test -x "${ENGINE_BIN}" || { echo "[alloy-thermo-mc] build produced no alloy_mc" >&2; exit 1; }

# 2b. Fail loudly and early if the binary is not actually visible from every node —
#     this is the multi-node trap above, and it is far cheaper to catch here than as
#     an execve() error after the ladder has been launched.
if [ "${SLURM_NNODES:-1}" -gt 1 ]; then
    if ! srun --ntasks="${SLURM_NNODES}" --ntasks-per-node=1 test -x "${ENGINE_BIN}"; then
        echo "[alloy-thermo-mc] ${ENGINE_BIN} is not visible on all ${SLURM_NNODES} nodes." >&2
        echo "[alloy-thermo-mc] ALLOYMC_SCRATCH_DIR must be on a SHARED filesystem." >&2
        exit 1
    fi
    echo "[alloy-thermo-mc] verified alloy_mc is visible on all ${SLURM_NNODES} nodes"
fi

# 3. Python for make_inputs.py / analyze.py (numpy + matplotlib only; seconds to install).
module load cray-python 2>/dev/null || module load python 2>/dev/null || true
python3 -m venv "${VENV}"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python3 -m pip install --no-cache-dir --quiet --upgrade pip
python3 -m pip install --no-cache-dir --quiet numpy matplotlib

export MPLBACKEND=Agg

echo "[alloy-thermo-mc] env ready on $(hostname): ranks=${NRANKS} shared=${SHARED}"

# 4. Run one composition. run_state_point.py runs SERIALLY here (no srun wrapper) and
#    launches `srun -n ${NRANKS} alloy_mc` itself — the rank count IS the ladder size,
#    so the parallel step has to be launched from inside the wrapper. Its --work-dir is
#    on the shared filesystem so every rank can read the generated input files.
"${VENV}/bin/python" -u "${RUN_DIR_Odo}/run_state_point.py" \
    --skill-root "${CLONE_DIR}" \
    --engine-bin "${ENGINE_BIN}" \
    --output-dir "${VISTA_OUT}" \
    --work-dir "${SHARED}/alloy-run" \
    --ranks "${NRANKS}" \
    --launcher srun \
    "$@"
