#!/bin/bash -l
# VISTA starts this in the job's source folder (RUN_DIR_Odo), which the project's
# IRI automation user the job runs as can only read. Everything it writes -- the
# checkpoints, and the final model forge-tune.py saves to the working directory --
# therefore goes under $VISTA_OUT.

: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Odo:?RUN_DIR_Odo not set}"

source /gpfs/wolf2/olcf/gen150/proj-shared/xforge/xforge-env.sh
export NCCL_SOCKET_IFNAME=hsn
export MIOPEN_DISABLE_CACHE=1
export OMP_NUM_THREADS=7

initial_lr=1e-3
min_lr=1e-5
warmup_epochs=5
num_epochs=25

differential_lr=True
freeze_llm=False

CMD="python -u ${RUN_DIR_Odo}/forge-tune.py \
    --input ${RUN_DIR_Odo}/Molten_Salt_Thermophysical_Properties.csv \
    --checkpoint-dir ${VISTA_OUT} \
    --initial-lr ${initial_lr} \
    --min-lr ${min_lr} \
    --warmup-epochs ${warmup_epochs} \
    --num-epochs ${num_epochs}"

if [ "$differential_lr" == "True" ]; then
    CMD="${CMD} --use-differential-lr"
fi

if [ "$freeze_llm" == "True" ]; then
    CMD="${CMD} --freeze-llm"
fi

if [ -n "$checkpoint" ]; then
    CMD="${CMD} --resume-from $checkpoint"
fi

cd "${VISTA_OUT}"
srun --nodes=${SLURM_NNODES} \
     --ntasks=$((SLURM_NNODES * 8)) \
     --gpus-per-node=8 \
     -c 7 \
     bash -c "source ${RUN_DIR_Odo}/setup_dist_vars.sh; ${CMD}"
