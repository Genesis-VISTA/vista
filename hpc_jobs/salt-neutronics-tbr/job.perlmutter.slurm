#!/bin/bash -l
# Perlmutter (NERSC) Slurm script for salt-neutronics-tbr via the NERSC IRI API.
#
# Runs inside vista's IRI `bash -l -c` invocation. Unlike OLCF (Odo/Frontier), the
# NERSC dispatcher's setup snippet (submit_job_mcp.py) only exports VISTA_OUT and
# mkdir's it — it does NOT cd, module purge, or export an HTTP proxy, because NERSC
# compute nodes have direct outbound network (git clone + pip work without a proxy).
# NERSC IRI also runs this script AS the allocated rank, so there is NO inner srun
# (one rank: node_count=1, processes_per_node=1). The state-point order ("$@") is the
# script_args forwarded verbatim — see README.md.
#
# This is a CPU-only, single-core, sub-second interpolation: it runs on the Perlmutter
# CPU partition (constraint=cpu, shared QOS), no GPU, no shifter, no pre-provisioned env.
#
# As with the Odo backend, the clone, the venv, and all caches go into NODE-LOCAL/
# SCRATCH workspace ($WORK), NOT $VISTA_OUT — only results.json lands in $VISTA_OUT, so
# the recursive output listing in get_hpc_job_status stays a single call.
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   VISTA_OUT              per-job output dir, already mkdir'd. results.json lands here.
#   RUN_DIR_Perlmutter    <nersc_remote_dir>/<job>/src — where vista staged run_state_point.py.
#   SALTN_REPO_URL        public salt-neutronics-skill repo to clone (the salt_neutronics code + data).
#   SALTN_REPO_REF        branch/tag/SHA to check out (default: main).

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Perlmutter:?RUN_DIR_Perlmutter not set}"

REPO_URL="${SALTN_REPO_URL:-https://github.com/jqyin/salt-neutronics-skill}"
REPO_REF="${SALTN_REPO_REF:-main}"

# Scratch for the clone, the venv, and all caches. $SCRATCH (Lustre) is always set on
# NERSC; fall back to node-local tmp. Keeping these OUT of $VISTA_OUT is what makes the
# post-run status check fast.
WORK="${SCRATCH:-${TMPDIR:-/tmp}}/saltn-${SLURM_JOB_ID:-$$}"
mkdir -p "${WORK}"
trap 'rm -rf "${WORK}"' EXIT
export HOME="${WORK}"               # pip/matplotlib drop dotfiles in HOME — keep them off $VISTA_OUT
export XDG_CACHE_HOME="${WORK}/.cache"
CLONE_DIR="${WORK}/salt-neutronics-skill"
VENV="${WORK}/venv"

# 1. Clone the analysis code at runtime (system git; NERSC compute nodes reach GitHub directly).
echo "[salt-neutronics-tbr] cloning ${REPO_URL}@${REPO_REF} -> ${CLONE_DIR}"
rm -rf "${CLONE_DIR}"
git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}"
if [ "${REPO_REF}" != "main" ]; then
    git -C "${CLONE_DIR}" fetch --depth 1 origin "${REPO_REF}"
    git -C "${CLONE_DIR}" checkout --detach FETCH_HEAD
fi

# 2. Python env. NERSC's `python` module already ships numpy/scipy/h5py/matplotlib, so
#    prefer it (no install, no network); only build a venv if something is missing.
module load python 2>/dev/null || module load cray-python 2>/dev/null || true
PYTHON=python3
if ! "${PYTHON}" -c "import numpy, scipy, h5py, matplotlib" 2>/dev/null; then
    echo "[salt-neutronics-tbr] base python missing deps; building a venv"
    "${PYTHON}" -m venv --system-site-packages "${VENV}"
    # shellcheck disable=SC1091
    source "${VENV}/bin/activate"
    python3 -m pip install --no-cache-dir --quiet --upgrade pip
    python3 -m pip install --no-cache-dir --quiet numpy scipy h5py matplotlib
    PYTHON="${VENV}/bin/python"
fi

export MPLBACKEND=Agg

echo "[salt-neutronics-tbr] env ready on $(hostname): python=${PYTHON}"

# 3. Run one TBR state point. run_state_point.py turns the flat order into the repo's
#    `salt_neutronics.cli tbr ... --json --output $VISTA_OUT/results.json`. NERSC IRI runs
#    this as the allocated rank, so no srun. Only results.json is written to $VISTA_OUT.
"${PYTHON}" -u "${RUN_DIR_Perlmutter}/run_state_point.py" \
    --skill-root "${CLONE_DIR}" \
    --output-dir "${VISTA_OUT}" \
    "$@"
