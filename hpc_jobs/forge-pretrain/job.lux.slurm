#!/bin/bash -l
# Lux Slurm body for forge-pretrain, adapted from forge@lux train/job.sb.
# Vista's Lux dispatcher prepends the #SBATCH header (account, nodes, walltime,
# --exclusive, log paths) and the env below, then submits over SSH. The checkout
# was updated on the login node by setup_lux.sh just before submission.
#
# Env vars provided by vista (submit_job_mcp._submit_lux_job):
#   VISTA_OUT        per-job output dir, already created
#   VISTA_JOB_DIR    <remote_dir>.out/forge-pretrain; holds the forge checkout
#   RUN_DIR_Lux      <remote_dir>.jobs/forge-pretrain/src (make_config.py, forge_common.sh)
#   LUX_ENV_SCRIPT   from cluster_defaults.json
# Positional args ("$@", from script_args): KEY=VALUE pairs, see README.md.

set -euo pipefail

: "${VISTA_OUT:?}" "${VISTA_JOB_DIR:?}" "${RUN_DIR_Lux:?}"
: "${LUX_ENV_SCRIPT:?LUX_ENV_SCRIPT not set; add to cluster_defaults.json}"

# shellcheck source=forge_common.sh
source "${RUN_DIR_Lux}/forge_common.sh"
forge_parse_args "$@"

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

# Built once per cluster, shared across runs. Lux and Frontier share the checkout
# but not a GPU architecture, so each gets its own build dir.
export TORCH_EXTENSIONS_DIR="${VISTA_JOB_DIR}/torch_extensions/lux"
mkdir -p "${TORCH_EXTENSIONS_DIR}"
export CUDAPATH="${ROCM_PATH}"

forge_launch lux.yml "${RUN_DIR_Lux}"
