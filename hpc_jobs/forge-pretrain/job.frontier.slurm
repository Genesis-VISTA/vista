#!/bin/bash -l
# Frontier Slurm body for forge-pretrain, via the OLCF moderate-enclave IRI API.
# Environment from forge@xforge train/job.sb (xforge module, hsn NICs); launched
# the same way as on Lux. The IRI service runs this once, on the head node;
# DeepSpeed's slurm launcher then sruns the ranks across the allocation.
#
# Env vars provided by vista's Frontier dispatcher (submit_job_mcp._submit_frontier_job):
#   VISTA_OUT          per-job output dir, already created
#   VISTA_JOB_DIR      <remote_dir>.out/forge-pretrain; holds the forge checkout
#   RUN_DIR_Frontier   <remote_dir>.<user>.jobs/forge-pretrain/src (prepare_forge.sh, make_config.py, forge_common.sh)
#   http(s)_proxy      OLCF proxy, for the checkout update below
#   FORGE_*            from cluster_defaults.json -> frontier.iri.environment
# Positional args ("$@", from script_args): KEY=VALUE pairs, see README.md.

set -euo pipefail

: "${VISTA_OUT:?}" "${VISTA_JOB_DIR:?}" "${RUN_DIR_Frontier:?}"

# shellcheck source=forge_common.sh
source "${RUN_DIR_Frontier}/forge_common.sh"
forge_parse_args "$@"

# No login-node step on the IRI path: update the checkout and data links here.
bash "${RUN_DIR_Frontier}/prepare_forge.sh"

ulimit -n 65536
set +u
module use /sw/aaims/crusher/modulefiles
module load xforge
set -u

export NCCL_SOCKET_IFNAME=hsn
export FI_CXI_ATS=0

# Built once per cluster, shared across runs; see job.lux.slurm.
export TORCH_EXTENSIONS_DIR="${VISTA_JOB_DIR}/torch_extensions/frontier"
mkdir -p "${TORCH_EXTENSIONS_DIR}"
export CUDAPATH="${ROCM_PATH}"

# IRI allocates one task per node, and srun reads SLURM_NTASKS_PER_NODE from its
# environment as a request: left set, it contradicts DeepSpeed's `srun -n <N x 8>`.
# (forge_launch sets SLURM_NTASKS to the full rank count.)
unset SLURM_NTASKS_PER_NODE

# lux.yml, not frontier.yml: the same launch as Lux -- DeepSpeed's slurm launcher
# with `deepspeed_slurm`, ranks from SLURM_PROCID / SLURM_LOCALID / SLURM_NTASKS --
# instead of frontier.yml's MPI discovery. The file holds nothing Lux-specific
# (output dirs, which make_config.py overrides, and the launcher); the Lux
# networking lives in job.lux.slurm's environment.
# xforge's job.sb forwards the extra vars to the ranks explicitly as well.
forge_launch lux.yml "${RUN_DIR_Frontier}" \
    CUDAPATH TORCH_EXTENSIONS_DIR NCCL_SOCKET_IFNAME FI_CXI_ATS
