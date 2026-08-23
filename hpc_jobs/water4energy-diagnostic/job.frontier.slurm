#!/bin/bash -l
# Frontier script for water4energy-diagnostic via the OLCF moderate-enclave IRI API.
#
# IMPORTANT: there are deliberately no #SBATCH directives here. Vista's Frontier
# dispatcher (submit_job_mcp.py) submits an IRI JobSpec — `executable: bash`,
# `arguments: ["-l", "-c", <this script, inlined>]` — so #SBATCH lines would be
# inert comments. Walltime and queue come from cluster_defaults.json
# (`frontier.duration` in seconds, `frontier.iri.queue_name`).
#
# No inner srun either: this is a 1-rank CPU job (node_count=1, no
# processes_per_node -> vista sets process_count=1), so it runs directly on the
# exclusive compute node. Same shape as hpc_jobs/example/job.frontier.slurm.
#
# Env vars provided by vista's IRI dispatcher + cluster_defaults.json:
#   VISTA_OUT          per-job output dir, already mkdir'd. Figures land here.
#   RUN_DIR_Frontier   <frontier_remote_dir>/<job>/src — where vista staged run_diagnostic.py.
#   W4E_DATA_DIR       read-only dir holding the pre-staged NetCDF climatologies (REQUIRED).
#   W4E_ENV            shared venv cache prefix. Optional: falls back to a per-job venv.
#   W4E_REPO_URL       public water4energy_diagnostic repo to clone.
#   W4E_REPO_REF       branch/tag/SHA to check out (default: main).
#   W4E_PYTHON_MODULE  Frontier python module (default: cray-python/3.11.7).
#   http(s)_proxy      OLCF HTTP proxy — set by vista; the only thing that needs it is
#                      the first-run pip install.
#
# The script_args string ("$@") is forwarded verbatim to run_diagnostic.py — see README.md.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"
: "${W4E_DATA_DIR:?W4E_DATA_DIR not set; add it to cluster_defaults.json iri.environment}"

export PYTHONNOUSERSITE=1
export HOME="${HOME:-${VISTA_OUT}}"

REPO_URL="${W4E_REPO_URL:-https://github.com/daliwang/water4energy_diagnostic}"
REPO_REF="${W4E_REPO_REF:-main}"
PYTHON_MODULE="${W4E_PYTHON_MODULE:-cray-python/3.11.7}"
CLONE_DIR="${VISTA_OUT}/water4energy_diagnostic"
REQUIRED_IMPORTS="import cartopy, matplotlib, netCDF4, numpy, scipy, shapely, xarray"

# ---------------------------------------------------------------------------
# 1. Python. The default Frontier stack is not enough on its own: cray-python
#    ships numpy/scipy but not cartopy, matplotlib, shapely, xarray, or netCDF4
#    (see FRONTIER_TEST_REPORT.md upstream), hence the venv below.
# ---------------------------------------------------------------------------
module load "${PYTHON_MODULE}"

# ---------------------------------------------------------------------------
# 2. Clone the diagnostic at runtime. Nothing is vendored into vista; the repo
#    carries the analysis program, the pinned requirements, the region polygon,
#    and the bundled Natural Earth coastline cache.
# ---------------------------------------------------------------------------
echo "[water4energy] cloning ${REPO_URL}@${REPO_REF} -> ${CLONE_DIR}"
rm -rf "${CLONE_DIR}"
git clone --depth 1 "${REPO_URL}" "${CLONE_DIR}"
if [ "${REPO_REF}" != "main" ]; then
    git -C "${CLONE_DIR}" fetch --depth 1 origin "${REPO_REF}"
    git -C "${CLONE_DIR}" checkout --detach FETCH_HEAD
fi

# ---------------------------------------------------------------------------
# 3. Self-healing environment.
#
#    Preferred: a shared venv at $W4E_ENV, reused across jobs — a warm run then
#    costs seconds instead of minutes of proxied wheel installs. That dir has to
#    be WRITABLE by the account vista submits under; Globus-created dirs are not
#    group-writable, so the shared cache may not be available on a fresh
#    deployment. Fallback: a per-job venv under $VISTA_OUT, which always works
#    and costs ~2-5 min.
#
#    A shared env that exists but is broken is NOT deleted here — another job may
#    be running out of it. We warn and fall back instead; an operator can remove
#    it deliberately. That keeps a bad cache from ever blocking a run.
# ---------------------------------------------------------------------------
env_ready() {
    local prefix="$1"
    [ -x "${prefix}/bin/python" ] || return 1
    "${prefix}/bin/python" -c "${REQUIRED_IMPORTS}" >/dev/null 2>&1
}

build_env() {
    local prefix="$1"
    echo "[water4energy] building venv at ${prefix} (cold start: ~2-5 min of wheels)"
    rm -rf "${prefix}"
    python3 -m venv "${prefix}"
    "${prefix}/bin/python" -m pip install --quiet --upgrade pip
    "${prefix}/bin/python" -m pip install --quiet -r "${CLONE_DIR}/requirements.txt"
}

ENV_PREFIX=""
ENV_STATE=""
SHARED_ENV="${W4E_ENV:-}"

if [ -n "${SHARED_ENV}" ] && env_ready "${SHARED_ENV}"; then
    ENV_PREFIX="${SHARED_ENV}"
    ENV_STATE="warm (shared cache)"
elif [ -n "${SHARED_ENV}" ] && [ ! -e "${SHARED_ENV}" ] \
     && mkdir -p "${SHARED_ENV%/*}" 2>/dev/null && [ -w "${SHARED_ENV%/*}" ]; then
    # Absent and creatable: build the shared cache under a lock so concurrent
    # submissions don't race. Lock failure is non-fatal — worst case two jobs
    # build and the second overwrites an env nobody has imported from yet.
    LOCK_FILE="${SHARED_ENV}.lock"
    exec 9>"${LOCK_FILE}" || true
    flock -w 240 9 || echo "[water4energy] WARNING: env lock timed out; building anyway"
    if env_ready "${SHARED_ENV}"; then          # another job won the race
        ENV_STATE="warm (shared cache, built concurrently)"
    else
        build_env "${SHARED_ENV}"
        ENV_STATE="cold (shared cache built this run)"
    fi
    flock -u 9 || true
    ENV_PREFIX="${SHARED_ENV}"
else
    if [ -n "${SHARED_ENV}" ] && [ -e "${SHARED_ENV}" ]; then
        echo "[water4energy] WARNING: shared env at ${SHARED_ENV} exists but fails" \
             "the import check; leaving it alone and building a per-job venv." >&2
        echo "[water4energy] WARNING: remove it by hand to re-enable the shared cache." >&2
    elif [ -n "${SHARED_ENV}" ]; then
        echo "[water4energy] NOTE: ${SHARED_ENV} is not creatable by this account;" \
             "building a per-job venv." >&2
    fi
    ENV_PREFIX="${VISTA_OUT}/.venv"
    build_env "${ENV_PREFIX}"
    ENV_STATE="cold (per-job venv)"
fi

if ! env_ready "${ENV_PREFIX}"; then
    echo "[water4energy] ERROR: venv at ${ENV_PREFIX} is missing required packages" >&2
    echo "[water4energy] Expected: ${REQUIRED_IMPORTS}" >&2
    echo "[water4energy] Check outbound HTTPS through the OLCF proxy and" \
         "${CLONE_DIR}/requirements.txt." >&2
    exit 1
fi

echo "[water4energy] env ${ENV_STATE}: ${ENV_PREFIX}"
"${ENV_PREFIX}/bin/python" - <<'PY'
for name in ("cartopy", "matplotlib", "netCDF4", "numpy", "scipy", "shapely", "xarray"):
    print(f"[water4energy]   {name}: {getattr(__import__(name), '__version__', '?')}")
PY

# ---------------------------------------------------------------------------
# 4. Run. Non-interactive matplotlib + a writable config dir are hard
#    requirements on a compute node; the bundled coastline cache (passed by the
#    wrapper) keeps cartopy from reaching for a download.
# ---------------------------------------------------------------------------
export MPLBACKEND=Agg
export MPLCONFIGDIR="${VISTA_OUT}/.mplconfig"
mkdir -p "${MPLCONFIGDIR}"

echo "[water4energy] running on $(hostname): out=${VISTA_OUT} data=${W4E_DATA_DIR}"
"${ENV_PREFIX}/bin/python" -u "${RUN_DIR_Frontier}/run_diagnostic.py" \
    --skill-root "${CLONE_DIR}" \
    --data-dir "${W4E_DATA_DIR}" \
    --output-dir "${VISTA_OUT}" \
    "$@"
