#!/bin/bash -l
# Perlmutter Slurm script for forge-tune via the NERSC IRI API.
# Mirrors vit-api-iri-toolkit/amscrot_vit.py: one srun + shifter invocation using
# the NERSC pytorch image, executing the training command inside the container.
#
# Env vars provided by vista's IRI dispatcher (mcp-server/.../submit_job_mcp.py):
#   VISTA_OUT             per-job output dir, already mkdir'd and collected on exit.
#                         Use for logs and small artifacts, NOT for checkpoints.
#   VISTA_KEEP            per-job dir kept on the cluster and never collected. Use for
#                         checkpoints, which a later run reaches via --resume-from.
#   VISTA_PM_IMAGE        shifter image (from cluster_defaults.json iri.image)
#   VISTA_PM_MODULE       shifter module (from cluster_defaults.json iri.module)
#   SLURM_GPUS_PER_NODE   GPUs per node (= IRI resources.processes_per_node)
#   SLURM_CPUS_PER_TASK   cpu_cores_per_process (set by Slurm from the IRI resources block)
#
# User-supplied env vars (set via cluster_defaults.json -> iri.environment, or
# via setup_perlmutter.sh, or by editing this file):
#   RUN_DIR_Perlmutter               dir containing forge-tune.py, hybrid_split.py,
#                         setup_dist_vars.sh, and the CSV
#   FORGE_MODEL_Perlmutter           path or HF id of the FORGE base model

set -euo pipefail

: "${RUN_DIR_Perlmutter:?RUN_DIR_Perlmutter not set; add to cluster_defaults.json iri.environment}"
: "${FORGE_MODEL_Perlmutter:?FORGE_MODEL_Perlmutter not set; add to cluster_defaults.json iri.environment}"

# Hyperparameters (mirrors hpc_jobs/forge-tune/job.odo.slurm; override by editing or env).
initial_lr="${initial_lr:-1e-3}"
min_lr="${min_lr:-1e-5}"
warmup_epochs="${warmup_epochs:-5}"
num_epochs="${num_epochs:-25}"
differential_lr="${differential_lr:-True}"
freeze_llm="${freeze_llm:-False}"
checkpoint="${checkpoint:-}"

# NCCL / HDF5 settings recommended by NERSC for shifter + multi-node distributed jobs.
export FI_MR_CACHE_MONITOR="${FI_MR_CACHE_MONITOR:-userfaultfd}"
export HDF5_USE_FILE_LOCKING="${HDF5_USE_FILE_LOCKING:-FALSE}"

# Assemble the training command run inside shifter.
TRAIN_CMD="python -u forge-tune.py \
    --model ${FORGE_MODEL_Perlmutter} \
    --input ${RUN_DIR_Perlmutter}/Molten_Salt_Thermophysical_Properties.csv \
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

# Derive MASTER_ADDR/MASTER_PORT on the host (scontrol isn't in shifter's PATH on
# Perlmutter). Shifter inherits these env vars into the container, and setup_dist_vars.sh
# honors the pre-set value.
export MASTER_ADDR=$(scontrol show hostname "${SLURM_NODELIST}" | head -n 1)
export MASTER_PORT="${MASTER_PORT:-29501}"

# Inside shifter: cd into the run dir, set DDP env vars, then run training.
INNER_CMD="cd ${RUN_DIR_Perlmutter} && source setup_dist_vars.sh && ${TRAIN_CMD}"

# IRI/Slurm already allocated process_count ranks (= node_count * processes_per_node
# from cluster_defaults.json). This script runs in parallel, once per rank, with Slurm
# setting SLURM_PROCID/SLURM_LOCALID per rank. No outer `srun` here — that would try to
# create a new step inside the already-fully-occupied allocation. Mirrors the vit-api
# pattern in vit-api-iri-toolkit/amscrot_vit.py.
exec shifter \
    --image="${VISTA_PM_IMAGE}" \
    --module="${VISTA_PM_MODULE}" \
    --env PYTHONUSERBASE="${VISTA_SCRATCH}" \
    bash -c "${INNER_CMD}"
