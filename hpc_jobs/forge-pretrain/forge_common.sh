# Sourced by job.lux.slurm and job.frontier.slurm: the parts of a forge-pretrain run
# that don't depend on the cluster. The job scripts keep only their environment.
#
# Every run happens in $VISTA_OUT, not in forge's train/ dir: the hostfile,
# .deepspeed_env, merged config, checkpoints, and logs are per run, so concurrent
# runs can share one checkout. The config is merged into one file
# (make_config.py) because NeoX rejects a key set in two config files, so the
# data path, iteration count, and output dirs can't be overridden by adding one.

# forge_parse_args "$@": set MODEL TRAIN_ITERS SAVE_INTERVAL LOG_INTERVAL
# LR_DECAY_ITERS LOAD_DIR from KEY=VALUE args (script_args), exiting 2 on anything else.
# The default model is per cluster ($FORGE_DEFAULT_MODEL, from cluster_defaults.json),
# because the largest model that fits depends on the GPUs' memory.
forge_parse_args() {
    MODEL="${FORGE_DEFAULT_MODEL:-forge-l}"
    TRAIN_ITERS=50
    SAVE_INTERVAL=0   # no checkpoint unless asked: forge-l checkpoints run to hundreds of GB
    LOG_INTERVAL=1
    LR_DECAY_ITERS=
    LOAD_DIR=
    local kv
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
}

# forge_launch CLUSTER_YML SRC_DIR [EXTRA_DEEPSPEED_ENV_VAR ...]
#   CLUSTER_YML  forge's cluster config under train/configs (lux.yml on both clusters)
#   SRC_DIR      where vista uploaded make_config.py
#   extra vars   exported vars to forward to every rank via .deepspeed_env, beyond
#                PATH / LD_LIBRARY_PATH / CPATH
forge_launch() {
    local cluster_yml="$1" src_dir="$2"
    shift 2
    local train_dir="${VISTA_JOB_DIR}/forge/train"
    local config="${VISTA_OUT}/config/${MODEL}.yml"

    echo "[forge-pretrain] job ${SLURM_JOB_ID} on ${SLURM_NNODES} node(s), model ${MODEL}"
    echo "[forge-pretrain] forge $(git -c safe.directory="${VISTA_JOB_DIR}/forge" -C "${VISTA_JOB_DIR}/forge" log -1 --format='%h %s')"

    cd "${VISTA_OUT}"
    umask 002

    # DeepSpeed's launcher forwards these to every rank.
    echo "PATH=$PATH" > .deepspeed_env
    echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}" >> .deepspeed_env
    echo "CPATH=${CPATH:-}" >> .deepspeed_env
    local var
    for var in "$@"; do
        echo "${var}=${!var:-}" >> .deepspeed_env
    done

    scontrol show hostnames "${SLURM_NODELIST}" | awk '{print $1" slots=8"}' > hostfile
    MASTER_ADDR="$(head -n 1 hostfile | awk '{print $1}')"
    export MASTER_ADDR
    export MASTER_PORT=29500
    # With `deepspeed_slurm` (lux.yml), NeoX sets WORLD_SIZE from SLURM_NTASKS in
    # every rank, and DeepSpeed's slurm launcher sruns with --export=ALL, so the
    # ranks see THIS value: it must be the full rank count. The Lux allocation
    # (--ntasks-per-node=8) already sets it; Frontier's (one task per node, from
    # IRI) says SLURM_NNODES, which would make every rank disagree about the
    # world size.
    export SLURM_NTASKS=$(( SLURM_NNODES * 8 ))
    "${ROCM_PATH}/bin/rocm-smi" || true

    # $VISTA_JOB_DIR/data: symlinks to $FORGE_DATA_DIR made by prepare_forge.sh,
    # in a dir NeoX can write its dataset index maps to.
    local config_args=(--out-dir "${VISTA_OUT}" --data-dir "${VISTA_JOB_DIR}/data"
                       --train-iters "${TRAIN_ITERS}" --log-interval "${LOG_INTERVAL}"
                       --save-interval "${SAVE_INTERVAL}")
    [ -n "${LR_DECAY_ITERS}" ] && config_args+=(--lr-decay-iters "${LR_DECAY_ITERS}")
    [ -n "${LOAD_DIR}" ] && config_args+=(--load-dir "${LOAD_DIR}")

    python -u "${src_dir}/make_config.py" \
        "${train_dir}/configs/${MODEL}.yml" "${train_dir}/configs/${cluster_yml}" \
        "${config}" "${config_args[@]}"

    python -u "${train_dir}/deepy.py" "${train_dir}/train.py" "${config}"
}
