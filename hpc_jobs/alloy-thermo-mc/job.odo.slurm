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
# IMPORTANT: the clone, the build, the venv, and the run directory go into NODE-LOCAL
# scratch ($WORK under /tmp), NOT $VISTA_OUT. Only results.json and the small analysis
# artifacts land in $VISTA_OUT. This keeps the job's output dir to a handful of files
# so `get_hpc_job_status`' recursive Globus listing of it stays one fast call instead
# of walking a build tree and a .git dir (which makes a status check hang for minutes).
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   VISTA_OUT         per-job output dir, already mkdir'd. results.json lands here.
#   RUN_DIR_Odo       <odo_remote_dir>/<job>/src — where vista staged run_state_point.py.
#   ALLOYMC_REPO_URL  public alloy-thermo-skill repo to clone (engine + scripts + DFT spec).
#   ALLOYMC_REPO_REF  branch/tag/SHA to check out (default: main).
#   http(s)_proxy     OLCF HTTP proxy (compute nodes have no direct outbound) — set by vista.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Odo:?RUN_DIR_Odo not set}"

REPO_URL="${ALLOYMC_REPO_URL:-https://github.com/jqyin/alloy-thermo-skill}"
REPO_REF="${ALLOYMC_REPO_REF:-main}"

# Ranks == temperature replicas. Prefer what Slurm allocated; fall back to 56/node
# (Odo nodes expose 8 x 7 usable cores, matching the skill's --tasks-per-node 56).
NRANKS="${SLURM_NTASKS:-$(( ${SLURM_NNODES:-1} * 56 ))}"

# Node-local scratch for the clone, the build, the venv and the run dir. Keeping
# these OUT of $VISTA_OUT is what makes the post-run status check fast.
WORK="${TMPDIR:-/tmp}/alloymc-${SLURM_JOB_ID:-$$}"
mkdir -p "${WORK}"
trap 'rm -rf "${WORK}"' EXIT
export HOME="${WORK}"               # pip/matplotlib drop dotfiles in HOME — keep them off $VISTA_OUT
export XDG_CACHE_HOME="${WORK}/.cache"
CLONE_DIR="${WORK}/alloy-thermo-skill"
VENV="${WORK}/venv"

# 1. Clone the engine + scripts at runtime (system git; proxy already exported by vista).
echo "[alloy-thermo-mc] cloning ${REPO_URL}@${REPO_REF} -> ${CLONE_DIR}"
rm -rf "${CLONE_DIR}"
git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}"
if [ "${REPO_REF}" != "main" ]; then
    git -C "${CLONE_DIR}" fetch --depth 1 origin "${REPO_REF}"
    git -C "${CLONE_DIR}" checkout --detach FETCH_HEAD
fi

# 2. Build the MPI engine. Cray PE: the C++ MPI wrapper is `CC`, so override the
#    Makefile's mpic++ default. No external libraries beyond MPI and C++17.
module load PrgEnv-gnu cray-mpich 2>/dev/null || true
echo "[alloy-thermo-mc] building alloy_mc with CXX=CC"
make -C "${CLONE_DIR}/engine" CXX=CC -j 8
ENGINE_BIN="${CLONE_DIR}/engine/alloy_mc"
test -x "${ENGINE_BIN}" || { echo "[alloy-thermo-mc] build produced no alloy_mc" >&2; exit 1; }

# 3. Python for make_inputs.py / analyze.py (numpy + matplotlib only; seconds to install).
module load cray-python 2>/dev/null || module load python 2>/dev/null || true
python3 -m venv "${VENV}"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python3 -m pip install --no-cache-dir --quiet --upgrade pip
python3 -m pip install --no-cache-dir --quiet numpy matplotlib

export MPLBACKEND=Agg

echo "[alloy-thermo-mc] env ready on $(hostname): ranks=${NRANKS} python=$(command -v python)"

# 4. Run one composition. run_state_point.py runs SERIALLY here (no srun wrapper) and
#    launches `srun -n ${NRANKS} alloy_mc` itself — the rank count IS the ladder size,
#    so the parallel step has to be launched from inside the wrapper. Only results.json
#    (plus summary.md / the two PNGs) is written to $VISTA_OUT.
"${VENV}/bin/python" -u "${RUN_DIR_Odo}/run_state_point.py" \
    --skill-root "${CLONE_DIR}" \
    --engine-bin "${ENGINE_BIN}" \
    --output-dir "${VISTA_OUT}" \
    --work-dir "${WORK}/alloy-run" \
    --ranks "${NRANKS}" \
    --launcher srun \
    "$@"
