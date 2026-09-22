#!/bin/bash -l
# Frontier script for refine-downscaling via the OLCF moderate-enclave IRI API.
#
# Runs inside vista's IRI `bash -l -c` invocation, which has already exported the
# OLCF HTTP proxy, set VISTA_OUT, and done `module purge`.
#
# IMPORTANT: there are deliberately no #SBATCH directives here. Vista's Frontier
# dispatcher (submit_job_mcp.py) submits an IRI JobSpec — `executable: bash`,
# `arguments: ["-l", "-c", <this script, inlined>]` — so #SBATCH lines would be
# inert comments. Walltime and queue come from cluster_defaults.json
# (`frontier.duration` in seconds, `frontier.iri.queue_name`).
#
# One node, one rank, one GPU: node_count=1 with no processes_per_node, so vista
# sets process_count=1 and the script runs on the head of an exclusive node. The
# inner `srun` below is what actually binds a GCD to the pipeline process —
# same shape as hpc_jobs/salt-chemistry-md/job.frontier.slurm.
#
# Env vars provided by vista's IRI dispatcher + cluster_defaults.json:
#   VISTA_OUT          per-job output dir, already mkdir'd. EVERYTHING we write lands here.
#   RUN_DIR_Frontier   <frontier_remote_dir>/<job>/src — where vista staged run_downscaling.py.
#   REFINE_BASE_DIR    read-only demo package root on Lustre (REQUIRED).
#   REFINE_ENV         pre-provisioned ROCm PyTorch conda env prefix (REQUIRED).
#   REFINE_CHECKPOINT  pretrained 6x checkpoint.
#   REFINE_DATA_DIR    prepared Stage 2 index (manifest, terrain, coords, masks, splits).
#   REFINE_INPUT_DIR   directory holding the coarse 0.25-degree Daymet NetCDFs.
#   http(s)_proxy      OLCF HTTP proxy — set by vista. Nothing here needs it; this job
#                      clones nothing and installs nothing.
#
# The script_args string ("$@") is forwarded verbatim to run_downscaling.py — see README.md.

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"
: "${REFINE_BASE_DIR:?REFINE_BASE_DIR not set; add it to cluster_defaults.json iri.environment}"
: "${REFINE_ENV:?REFINE_ENV not set; add it to cluster_defaults.json iri.environment}"

# HOME is not carried by amscrot's IRI service env, and conda's activation hooks
# break silently without it. VISTA_OUT is guaranteed writable and per-job-id.
export HOME="${VISTA_OUT}"
# Never let a stray ~/.local/lib python shadow the curated env's torch.
export PYTHONNOUSERSITE=1

# `inherit_environment` is left at its default (True) by the dispatcher so Slurm's
# SLURM_* vars survive, which means the IRI service host's PYTHONPATH comes along
# too. A foreign PYTHONPATH in front of a ROCm torch is a segfault at import, so
# drop it here and set it explicitly after the env is active.
unset PYTHONPATH

# ---------------------------------------------------------------------------
# 1. Environment. Versions match the demo package's own scripts/frontier_env.sh
#    (Python 3.12 / ROCm 6.4.1 / PyTorch 2.8.0+rocm6.4). Overridable by env for
#    a facility upgrade without touching this file.
# ---------------------------------------------------------------------------
module load "${REFINE_PRGENV_MODULE:-PrgEnv-gnu/8.6.0}"
module load "${REFINE_ROCM_MODULE:-rocm/6.4.1}"
module load "${REFINE_ACCEL_MODULE:-craype-accel-amd-gfx90a}"
module load "${REFINE_MINIFORGE_MODULE:-miniforge3/23.11.0-0}"
export LD_LIBRARY_PATH="${CRAY_LD_LIBRARY_PATH:-}:${LD_LIBRARY_PATH:-}"

if [ ! -d "${REFINE_ENV}" ]; then
    echo "[refine] ERROR: conda env not found at ${REFINE_ENV}" >&2
    echo "[refine] This job depends on a pre-provisioned ROCm PyTorch env and" \
         "deliberately does NOT build one — a multi-GB ROCm build inside a GPU" \
         "job's walltime is a support ticket, not a retry." >&2
    echo "[refine] Fix REFINE_ENV in hpc_jobs/refine-downscaling/cluster_defaults.json" \
         "or restore the env at that path." >&2
    exit 1
fi

eval "$(conda shell.bash hook)"
conda activate "${REFINE_ENV}"

# The pipelines import `refine_downscaling` from the demo root. Running a script
# by absolute path already puts its directory on sys.path[0], but the wrapper
# also imports the package directly, so make it explicit.
export PYTHONPATH="${REFINE_BASE_DIR}"

# ---------------------------------------------------------------------------
# 2. Caches. MIOpen keeps a SQLite perf DB and a compiled-kernel cache; both
#    belong on node-local /tmp, never on Lustre (SQLite over Lustre locks badly).
#    Tagged by job id so concurrent submissions cannot collide.
# ---------------------------------------------------------------------------
_cache_root="/tmp/refine-${USER:-vista}-${SLURM_JOB_ID:-$$}"
mkdir -p "${_cache_root}/miopen"
export MIOPEN_USER_DB_PATH="${_cache_root}/miopen"
export MIOPEN_CUSTOM_CACHE_DIR="${_cache_root}/miopen"

# Headless plotting for the quicklook / spatial-statistics figures.
export MPLBACKEND=Agg
export MPLCONFIGDIR="${VISTA_OUT}/.mplconfig"
mkdir -p "${MPLCONFIGDIR}"

# ---------------------------------------------------------------------------
# 3. Run. cwd is VISTA_OUT, never the demo root: REFINE_BASE_DIR is read-only
#    shared space and a stray relative write must land in our own output dir.
#    All pipeline paths are passed absolute by the wrapper.
# ---------------------------------------------------------------------------
cd "${VISTA_OUT}"

echo "[refine] host=$(hostname) python=$(which python)"
echo "[refine] base=${REFINE_BASE_DIR}"
echo "[refine] env=${REFINE_ENV}"
echo "[refine] out=${VISTA_OUT}"
echo "[refine] GPU env: ROCR_VISIBLE_DEVICES=${ROCR_VISIBLE_DEVICES:-unset}" \
     "HIP_VISIBLE_DEVICES=${HIP_VISIBLE_DEVICES:-unset}"

srun -N1 -n1 -c7 --gpus-per-task=1 --gpu-bind=closest \
    python -u "${RUN_DIR_Frontier}/run_downscaling.py" \
    --base-dir "${REFINE_BASE_DIR}" \
    --data-dir "${REFINE_DATA_DIR:-${REFINE_BASE_DIR}/daymet/prepared}" \
    --input-dir "${REFINE_INPUT_DIR:-${REFINE_BASE_DIR}/daymet/data}" \
    --checkpoint "${REFINE_CHECKPOINT:-${REFINE_BASE_DIR}/artifacts/runs/refine_stage2_v1/best.pt}" \
    --env-prefix "${REFINE_ENV}" \
    --output-dir "${VISTA_OUT}" \
    "$@"
