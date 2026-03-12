#!/bin/bash -l
#SBATCH -J forge-tune
#SBATCH -t 0:20:00
#SBATCH -A stf218
#SBATCH -N 16
##SBATCH -q debug 
#SBATCH --exclusive
##SBATCH --ntasks-per-node=8

module use /sw/aaims/crusher/modulefiles
module load xforge 
export NCCL_SOCKET_IFNAME=hsn
export MIOPEN_DISABLE_CACHE=1
export OMP_NUM_THREADS=7

initial_lr=1e-3
min_lr=1e-5
warmup_epochs=5
num_epochs=25

differential_lr=True
freeze_llm=False

CMD="python -u forge-tune.py \
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

srun --nodes=${SLURM_NNODES} \
     --ntasks=$((SLURM_NNODES * 8)) \
     --gpus-per-node=8 \
     -c 7 \
     bash -c "source setup_dist_vars.sh; ${CMD}"
