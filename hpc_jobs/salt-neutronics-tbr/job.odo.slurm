#!/bin/bash -l
# Odo (OLCF open enclave) Slurm script for salt-neutronics-tbr via the IRI API.
#
# Runs inside vista's IRI `bash -l -c` invocation, which has ALREADY (see the setup
# snippet in submit_job_mcp.py) exported VISTA_OUT and mkdir'd it, exported the OLCF
# HTTP proxy, set HOME, run `module purge`, and `cd`'d into RUN_DIR_Odo (where vista
# vista-materialized run_state_point.py). The state-point order ("$@") is the script_args
# forwarded verbatim — see README.md.
#
# This is a CPU-only, single-core, sub-second interpolation: no GPU, no GPU binding,
# no pre-provisioned conda env. We clone the public repo and build a throwaway venv at
# runtime (light deps -> seconds), then run one TBR query.
#
# IMPORTANT: the clone and the venv go into $VISTA_SCRATCH, NOT $VISTA_OUT. Only
# results.json lands in $VISTA_OUT, which vista uploads verbatim — anything left there
# is shipped, so working files belong in scratch (vista deletes it after the upload).
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   VISTA_OUT        per-job output dir, already mkdir'd. results.json lands here.
#   VISTA_SCRATCH    per-job working dir, already mkdir'd. NOT uploaded; deleted after.
#   RUN_DIR_Odo      where vista materialized run_state_point.py.
#   SALTN_REPO_URL   public salt-neutronics-skill repo to clone (the salt_neutronics code + data).
#   SALTN_REPO_REF   branch/tag/SHA to check out (default: main).
#   http(s)_proxy    OLCF HTTP proxy (compute nodes have no direct outbound) — set by vista.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${VISTA_SCRATCH:?VISTA_SCRATCH not set}"
: "${RUN_DIR_Odo:?RUN_DIR_Odo not set}"

REPO_URL="${SALTN_REPO_URL:-https://github.com/jqyin/salt-neutronics-skill}"
REPO_REF="${SALTN_REPO_REF:-main}"

# Scratch for the clone, the venv, and all caches (pip/matplotlib). Keeping these OUT
# of $VISTA_OUT is what keeps the uploaded output to a single results.json.
# No cleanup trap needed: vista removes $VISTA_SCRATCH after it uploads the output.
WORK="${VISTA_SCRATCH}/saltn"
mkdir -p "${WORK}"
export HOME="${WORK}"               # pip/matplotlib drop dotfiles in HOME — keep them off $VISTA_OUT
export XDG_CACHE_HOME="${WORK}/.cache"
CLONE_DIR="${WORK}/salt-neutronics-skill"
VENV="${WORK}/venv"

# 1. Clone the analysis code at runtime (system git; proxy already exported by vista).
echo "[salt-neutronics-tbr] cloning ${REPO_URL}@${REPO_REF} -> ${CLONE_DIR}"
rm -rf "${CLONE_DIR}"
git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}"
if [ "${REPO_REF}" != "main" ]; then
    git -C "${CLONE_DIR}" fetch --depth 1 origin "${REPO_REF}"
    git -C "${CLONE_DIR}" checkout --detach FETCH_HEAD
fi

# 2. Build a throwaway Python env in node-local scratch. Deps are light
#    (numpy/scipy/h5py/matplotlib): a fresh venv installs in seconds, no conda needed.
module load cray-python 2>/dev/null || module load python 2>/dev/null || true
python3 -m venv "${VENV}"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python3 -m pip install --no-cache-dir --quiet --upgrade pip
python3 -m pip install --no-cache-dir --quiet numpy scipy h5py matplotlib

export MPLBACKEND=Agg

echo "[salt-neutronics-tbr] env ready on $(hostname): python=$(command -v python)"

# 3. Run one TBR state point. run_state_point.py turns the flat order into the repo's
#    `salt_neutronics.cli tbr ... --json --output $VISTA_OUT/results.json`. Use the
#    venv interpreter explicitly so the activated env survives the srun task launch.
#    Only results.json is written to $VISTA_OUT (everything else stays in $WORK).
srun -N1 -n1 "${VENV}/bin/python" -u "${RUN_DIR_Odo}/run_state_point.py" \
    --skill-root "${CLONE_DIR}" \
    --output-dir "${VISTA_OUT}" \
    "$@"
