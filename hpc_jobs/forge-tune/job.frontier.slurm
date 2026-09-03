#!/bin/bash -l
# Frontier Slurm script for forge-tune via the OLCF moderate-enclave IRI API.
# Pattern mirrors hpc_jobs/forge-tune/job.odo.slurm (Odo): the IRI submission allocates
# the node with a single batch task, and we use an inner `srun -n 8` here to spawn
# the 8 ranks needed for distributed training (one per GCD).
#
# Note: this differs from job.perlmutter.slurm, which runs WITHOUT an inner srun
# because Perlmutter's IRI service launches the script once per rank itself. On
# Frontier the IRI service runs the script once on the head, so we srun explicitly.
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py):
#   VISTA_OUT            per-job output dir, already mkdir'd and uploaded on exit.
#                        Use for logs and small artifacts, NOT for checkpoints.
#   VISTA_KEEP           per-job dir kept on the cluster and never uploaded. Use for
#                        checkpoints, which a later run reaches via --resume-from.
#   RUN_DIR_Frontier     <frontier_remote_dir>/<job>/src (scp'd by vista)
#   FORGE_MODEL_Frontier path to FORGE base model weights (from cluster_defaults.json
#                        -> iri.environment).
#   SLURM_NNODES         node count from the IRI allocation
#   http(s)_proxy        OLCF HTTP proxy (Frontier compute nodes have no direct outbound)

set -euo pipefail

: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set; add to cluster_defaults.json iri.environment}"
: "${FORGE_MODEL_Frontier:?FORGE_MODEL_Frontier not set; add to cluster_defaults.json iri.environment}"

# Load the xforge module (PyTorch + ROCm + FORGE deps preinstalled).
module use /sw/aaims/crusher/modulefiles
module load xforge

# Frontier networking / runtime knobs (mirrors Odo's job.odo.slurm).
export NCCL_SOCKET_IFNAME="${NCCL_SOCKET_IFNAME:-hsn}"
export MIOPEN_DISABLE_CACHE="${MIOPEN_DISABLE_CACHE:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-7}"

# Hyperparameters (mirrors job.odo.slurm + job.perlmutter.slurm; override by editing or env).
initial_lr="${initial_lr:-1e-3}"
min_lr="${min_lr:-1e-5}"
warmup_epochs="${warmup_epochs:-5}"
num_epochs="${num_epochs:-25}"
differential_lr="${differential_lr:-True}"
freeze_llm="${freeze_llm:-False}"
checkpoint="${checkpoint:-}"

TRAIN_CMD="python -u forge-tune.py \
    --model ${FORGE_MODEL_Frontier} \
    --input ${RUN_DIR_Frontier}/Molten_Salt_Thermophysical_Properties.csv \
    --checkpoint-dir ${VISTA_KEEP} \
    --log-dir ${VISTA_OUT} \
    --initial-lr ${initial_lr} \
    --min-lr ${min_lr} \
    --warmup-epochs ${warmup_epochs} \
    --num-epochs ${num_epochs}"

if [ "${differential_lr}" = "True" ]; then
    TRAIN_CMD="${TRAIN_CMD} --use-differential-lr"
fi
if [ "${freeze_llm}" = "True" ]; then
    TRAIN_CMD="${TRAIN_CMD} --freeze-llm"
fi
if [ -n "${checkpoint}" ]; then
    TRAIN_CMD="${TRAIN_CMD} --resume-from ${checkpoint}"
fi

cd "${RUN_DIR_Frontier}"

# 8 ranks per node × N nodes. setup_dist_vars.sh sets per-rank RANK / LOCAL_RANK /
# WORLD_SIZE / MASTER_ADDR / MASTER_PORT from SLURM_PROCID, SLURM_LOCALID, SLURM_NTASKS.
srun --nodes=${SLURM_NNODES} \
     --ntasks=$((SLURM_NNODES * 8)) \
     --gpus-per-node=8 \
     -c 7 \
     bash -c "source setup_dist_vars.sh; ${TRAIN_CMD}"

