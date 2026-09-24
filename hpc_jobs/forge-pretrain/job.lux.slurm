#!/bin/bash -l
# Lux Slurm body for forge-pretrain, adapted from forge@lux train/job.sb.
# Vista's Lux dispatcher prepends the #SBATCH header (account csc708, nodes,
# walltime, --exclusive, log paths) and the env below, then submits over SSH.
#
# Differences from job.sb, all so concurrent runs can share one checkout:
#   - runs in $VISTA_OUT, not train/: hostfile, .deepspeed_env, checkpoints, and
#     logs are per run;
#   - one merged config (make_config.py) instead of `-d configs MODEL.yml lux.yml`,
#     because NeoX rejects a key set in two config files, so the data path,
#     iteration count, and output dirs can't be overridden by adding a file;
#   - no per-node `ssh rocm-smi` sweep (rocm-smi once, on the head node).
#
# Env vars provided by vista (submit_job_mcp._submit_lux_job):
#   VISTA_OUT        per-job output dir, already created
#   VISTA_JOB_DIR    <lux_remote_dir>/forge-pretrain; holds the forge checkout
#                    that setup_lux.sh updated just before submission
#   RUN_DIR_Lux      $VISTA_JOB_DIR/src (make_config.py, uploaded by vista)
#   LUX_ENV_SCRIPT   from cluster_defaults.json
# Positional args ("$@", from script_args): KEY=VALUE pairs, see README.md.

set -euo pipefail

: "${VISTA_OUT:?}" "${VISTA_JOB_DIR:?}" "${RUN_DIR_Lux:?}"
: "${LUX_ENV_SCRIPT:?LUX_ENV_SCRIPT not set; add to cluster_defaults.json}"

MODEL=forge-l
TRAIN_ITERS=50
SAVE_INTERVAL=0   # no checkpoint unless asked: forge-l checkpoints run to hundreds of GB
LOG_INTERVAL=1
LR_DECAY_ITERS=
LOAD_DIR=
for kv in "$@"; do
    case "${kv}" in
        MODEL=*|TRAIN_ITERS=*|SAVE_INTERVAL=*|LOG_INTERVAL=*|LR_DECAY_ITERS=*|LOAD_DIR=*)
            printf -v "${kv%%=*}" '%s' "${kv#*=}" ;;
        *)
            echo "[forge-pretrain] ERROR: unknown script arg '${kv}'" >&2
            echo "[forge-pretrain] expected KEY=VALUE with KEY in MODEL TRAIN_ITERS SAVE_INTERVAL LOG_INTERVAL LR_DECAY_ITERS LOAD_DIR" >&2
            exit 2 ;;
    esac
done
case "${MODEL}" in
    forge-s|forge-m|forge-l) ;;
    *) echo "[forge-pretrain] ERROR: MODEL must be forge-s, forge-m, or forge-l (got '${MODEL}')" >&2; exit 2 ;;
esac

TRAIN_DIR="${VISTA_JOB_DIR}/forge/train"
echo "[forge-pretrain] job ${SLURM_JOB_ID} on ${SLURM_NNODES} node(s), model ${MODEL}"
echo "[forge-pretrain] forge $(git -c safe.directory="${VISTA_JOB_DIR}/forge" -C "${VISTA_JOB_DIR}/forge" log -1 --format='%h %s')"

# The env script is not written for `set -u`.
set +u
# shellcheck disable=SC1090
source "${LUX_ENV_SCRIPT}"
set -u

export TORCH_BLAS_PREFER_HIPBLASLT=0

# Lux networking (AMD Pensando ionic NICs), verbatim from job.sb.
#export NCCL_DEBUG=INFO
export NCCL_SOCKET_IFNAME=bond0
export NCCL_IB_HCA="ionic_0,ionic_1,ionic_2,ionic_3,ionic_4,ionic_5,ionic_6,ionic_7"
export NCCL_IB_GID_INDEX=1
export NCCL_GDR_FLUSH_DISABLE=1
export RCCL_GDR_FLUSH_GPU_MEM_NO_RELAXED_ORDERING=0
export NCCL_GDRCOPY_ENABLE=0
export NCCL_IB_PCI_RELAXED_ORDERING=1
export NCCL_IB_QPS_PER_CONNECTION=1
export HSA_NO_SCRATCH_RECLAIM=1
export NCCL_IB_TC=104
export NCCL_IGNORE_CPU_AFFINITY=1
export NCCL_PXN_DISABLE=0
export NCCL_DMABUF_ENABLE=1
export NCCL_IB_USE_INLINE=1
export IONIC_LOCKFREE=all
export RCCL_AINIC_ROCE=1
export LD_PRELOAD=/usr/lib64/libionic.so.1

# Shared across runs so DeepSpeed ops are built once (setup_lux.sh made it group-writable).
export TORCH_EXTENSIONS_DIR="${VISTA_JOB_DIR}/torch_extensions"
export CUDAPATH="${ROCM_PATH}"

cd "${VISTA_OUT}"
umask 002

# DeepSpeed's launcher forwards these to every rank.
echo "PATH=$PATH" > .deepspeed_env
echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}" >> .deepspeed_env
echo "CPATH=${CPATH:-}" >> .deepspeed_env

scontrol show hostnames "${SLURM_NODELIST}" | awk '{print $1" slots=8"}' > hostfile
MASTER_ADDR="$(head -n 1 hostfile | awk '{print $1}')"
export MASTER_ADDR
export MASTER_PORT=29500
"${ROCM_PATH}/bin/rocm-smi" || true

# $VISTA_JOB_DIR/data: symlinks to $FORGE_DATA_DIR made by setup_lux.sh, in a dir
# NeoX can write its dataset index maps to.
config_args=(--out-dir "${VISTA_OUT}" --data-dir "${VISTA_JOB_DIR}/data"
             --train-iters "${TRAIN_ITERS}" --log-interval "${LOG_INTERVAL}"
             --save-interval "${SAVE_INTERVAL}")
[ -n "${LR_DECAY_ITERS}" ] && config_args+=(--lr-decay-iters "${LR_DECAY_ITERS}")
[ -n "${LOAD_DIR}" ] && config_args+=(--load-dir "${LOAD_DIR}")

python -u "${RUN_DIR_Lux}/make_config.py" \
    "${TRAIN_DIR}/configs/${MODEL}.yml" "${TRAIN_DIR}/configs/lux.yml" \
    "${VISTA_OUT}/config/${MODEL}.yml" "${config_args[@]}"

python -u "${TRAIN_DIR}/deepy.py" "${TRAIN_DIR}/train.py" "${VISTA_OUT}/config/${MODEL}.yml"
