#!/bin/bash -l
# Odo (OLCF open enclave) Slurm script for vae-orderparam via the IRI API.
#
# Runs inside vista's IRI `bash -l -c` invocation (VISTA_OUT exported and mkdir'd, OLCF
# proxy exported, HOME set, `module purge` run). The order ("$@") is script_args
# forwarded verbatim — see README.md.
#
# This is the TRAINING stage: PyTorch on GPU, not the MPI engine. It reads the snapshots
# the `deepthermo-wl --mode collect` job left in the shared workspace and writes the
# exported TorchScript model back into the same workspace, where the sampling stage
# looks for it.
#
# Single task: train_vae.py is a single-process entry point here. (Multi-GPU is the same
# entry point under torchrun — see the submodule README — not wired up yet.)
#
# Env vars from the dispatcher + cluster_defaults.json:
#   VISTA_OUT                 per-job output dir, already mkdir'd.
#   RUN_DIR_Odo               where vista Globus-staged run_training.py.
#   DEEPTHERMO_REPO_URL/REF   public DeepThermo-WL repo + branch (default: vae-proposal).
#   XFORGE_ENV                optional override for the xforge env script
#                             (default: /gpfs/wolf2/olcf/gen150/proj-shared/xforge/xforge-env.sh).
#   DEEPTHERMO_WS_ROOT        optional override for the persistent workspace root.
#   (the workspace NAME is a script_args flag, not an env var — submit_hpc_job
#    passes no environment, so it has to travel with the order.)

set -euo pipefail

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Odo:?RUN_DIR_Odo not set}"


REPO_URL="${DEEPTHERMO_REPO_URL:-https://github.com/jqyin/DeepThermo-WL}"
REPO_REF="${DEEPTHERMO_REPO_REF:-vae-proposal}"
JOBID="${SLURM_JOB_ID:-$$}"

# The SAME persistent workspace the collect stage populated.
WS_ROOT="${DEEPTHERMO_WS_ROOT:-$(dirname "${VISTA_OUT}")/deepthermo-ws}"

# Ephemeral clone. Node-local is fine: only this one task reads it.
WORK="${TMPDIR:-/tmp}/vaeop-${JOBID}"
mkdir -p "${WORK}"
trap 'rm -rf "${WORK}"' EXIT
export HOME="${WORK}"
export XDG_CACHE_HOME="${WORK}/.cache"
CLONE_DIR="${WORK}/DeepThermo-WL"

# Same environment as the forge-tune job. On Odo xforge is a shell script to source,
# not a module (see hpc_jobs/forge-tune/job.odo.slurm) — it brings PyTorch + ROCm.
source "${XFORGE_ENV:-/gpfs/wolf2/olcf/gen150/proj-shared/xforge/xforge-env.sh}"
export MPLBACKEND=Agg

# Clone WITH the submodule — vae-modeling IS the training code.
echo "[vae-orderparam] cloning ${REPO_URL}@${REPO_REF} (+ vae-modeling submodule)"
git clone --depth 1 --branch "${REPO_REF}" --recurse-submodules --shallow-submodules \
    "${REPO_URL}" "${CLONE_DIR}"

PYTHON="${DEEPTHERMO_PYTHON:-$(command -v python3)}"
"${PYTHON}" -c "import torch" 2>/dev/null || {
    echo "[vae-orderparam] ${PYTHON} has no torch — did XFORGE_ENV source cleanly?" >&2; exit 1; }

echo "[vae-orderparam] ready on $(hostname): ws_root=${WS_ROOT}"

srun -N1 -n1 --gpus-per-task=1 "${PYTHON}" -u "${RUN_DIR_Odo}/run_training.py" \
    --skill-root "${CLONE_DIR}" \
    --output-dir "${VISTA_OUT}" \
    --workspace-root "${WS_ROOT}" \
    --python "${PYTHON}" \
    "$@"
