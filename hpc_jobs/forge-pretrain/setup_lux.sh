#!/bin/bash -l
# Lux setup for forge-pretrain. Runs on the Lux LOGIN node (over SSH, as the user)
# before sbatch -- compute nodes can't clone, and a failure here fails the submit
# call instead of a queued job.
#
#   1. Clone or update the shared forge checkout to the latest $FORGE_BRANCH.
#   2. Check the tokenized corpus and the Lux env script are readable, and link
#      the corpus into $VISTA_JOB_DIR/data (see below for why).
#
# Env vars exported by vista's Lux dispatcher (submit_job_mcp._submit_lux_job):
#   VISTA_JOB_DIR    <lux_remote_dir>/forge-pretrain (shared by every csc708 user)
#   http(s)_proxy    OLCF proxy (the login node has no direct outbound network)
#   FORGE_REPO_URL, FORGE_BRANCH, FORGE_DATA_DIR, LUX_ENV_SCRIPT
#                    from cluster_defaults.json -> lux.iri.environment

set -euo pipefail

: "${VISTA_JOB_DIR:?VISTA_JOB_DIR not set}"
: "${FORGE_REPO_URL:?FORGE_REPO_URL not set; add to cluster_defaults.json}"
: "${FORGE_BRANCH:?FORGE_BRANCH not set; add to cluster_defaults.json}"
: "${FORGE_DATA_DIR:?FORGE_DATA_DIR not set; add to cluster_defaults.json}"
: "${LUX_ENV_SCRIPT:?LUX_ENV_SCRIPT not set; add to cluster_defaults.json}"

# Group-writable, so the next csc708 member can update the checkout and reuse the
# JIT-built kernels that land inside it.
umask 002
mkdir -p "${VISTA_JOB_DIR}/torch_extensions"

repo="${VISTA_JOB_DIR}/forge"
# The checkout is owned by whoever cloned it first; without safe.directory git
# refuses to touch it for anyone else.
git_() { git -c safe.directory="${repo}" -C "${repo}" "$@"; }

# Serialize concurrent submissions updating the same checkout.
exec 9>"${VISTA_JOB_DIR}/.forge-checkout.lock"
if ! flock -w 600 9; then
    echo "[setup_lux] WARNING: could not lock the checkout; updating without the lock" >&2
fi

if [ -d "${repo}/.git" ]; then
    git_ fetch -q --depth 1 origin "${FORGE_BRANCH}"
    # Pull the latest: the shared checkout carries no local work, so a forced
    # checkout of what was fetched is the whole update. Untracked build products
    # (JIT-built fused kernels) survive it.
    git_ checkout -q -f -B "${FORGE_BRANCH}" FETCH_HEAD
else
    rm -rf "${repo}.tmp"
    git clone -q --depth 1 --branch "${FORGE_BRANCH}" \
        --config core.sharedRepository=group \
        "${FORGE_REPO_URL}" "${repo}.tmp"
    mv "${repo}.tmp" "${repo}"
fi
echo "[setup_lux] forge ${FORGE_BRANCH} at $(git_ log -1 --format='%h %cd %s' --date=short)"

for f in train/deepy.py train/train.py train/configs/lux.yml; do
    [ -f "${repo}/${f}" ] || { echo "[setup_lux] ERROR: ${repo}/${f} missing after checkout" >&2; exit 1; }
done

for f in all_text_document.bin all_text_document.idx all_vocab.json; do
    [ -r "${FORGE_DATA_DIR}/${f}" ] || { echo "[setup_lux] ERROR: ${FORGE_DATA_DIR}/${f} not readable" >&2; exit 1; }
done
[ -r "${LUX_ENV_SCRIPT}" ] || { echo "[setup_lux] ERROR: ${LUX_ENV_SCRIPT} not readable" >&2; exit 1; }

# Training reads the corpus through symlinks in a csc708-writable dir, because
# NeoX writes its dataset index maps (<prefix>_train_indexmap_<N>ns_*.npy) NEXT
# TO the data prefix on first use -- and FORGE_DATA_DIR is read-only to everyone
# but its owner. Here they are cached for every csc708 user, and a later run of
# the same shape (same N = iterations x global batch) reuses them.
data_links="${VISTA_JOB_DIR}/data"
mkdir -p "${data_links}"
for f in all_text_document.bin all_text_document.idx all_vocab.json; do
    ln -sfn "${FORGE_DATA_DIR}/${f}" "${data_links}/${f}"
done
echo "[setup_lux] data and env OK (index maps cached in ${data_links})"
