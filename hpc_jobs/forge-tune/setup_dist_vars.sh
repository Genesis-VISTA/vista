export RANK=$SLURM_PROCID
export LOCAL_RANK=$SLURM_LOCALID
export WORLD_SIZE=$SLURM_NTASKS
# Honor a pre-set MASTER_ADDR (e.g. exported on the Perlmutter host before entering
# shifter, where scontrol isn't in PATH). Fall back to scontrol on Frontier / Odo.
if [ -z "${MASTER_ADDR:-}" ]; then
    export MASTER_ADDR=$(scontrol show hostname ${SLURM_NODELIST} | head -n 1)
fi
export MASTER_PORT=${MASTER_PORT:-29501} # default from torch launcher

