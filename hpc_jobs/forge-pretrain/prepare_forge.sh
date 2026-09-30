#!/bin/bash
# Bring the shared forge checkout and data links up to date. Shared by both clusters:
#   Lux:      run by setup_lux.sh on the login node, before sbatch.
#   Frontier: run by job.frontier.slurm at job start (the IRI path has no
#             login-node step; the head compute node reaches GitHub via the proxy).
# The checkout under a remote_dir is shared by every job submitted there, and is
# written by different users (researchers over SSH on Lux; the project's IRI
# automation user on Frontier), hence group permissions, safe.directory, and the
# lock.
#
#   1. Clone or update $VISTA_JOB_DIR/forge to the latest $FORGE_BRANCH.
#   2. Check the tokenized corpus is readable, and link it into $VISTA_JOB_DIR/data.
#
# Env: VISTA_JOB_DIR (from vista); FORGE_REPO_URL, FORGE_BRANCH, FORGE_DATA_DIR
# (cluster_defaults.json); http(s)_proxy.

set -euo pipefail

: "${VISTA_JOB_DIR:?VISTA_JOB_DIR not set}"
: "${FORGE_REPO_URL:?FORGE_REPO_URL not set; add to cluster_defaults.json}"
: "${FORGE_BRANCH:?FORGE_BRANCH not set; add to cluster_defaults.json}"
: "${FORGE_DATA_DIR:?FORGE_DATA_DIR not set; add to cluster_defaults.json}"

# Group-writable, so the next project member (or automation user) can update the
# checkout and reuse the caches that land beside it.
umask 002
mkdir -p "${VISTA_JOB_DIR}/torch_extensions"

repo="${VISTA_JOB_DIR}/forge"
# The checkout is owned by whoever cloned it first; without safe.directory git
# refuses to touch it for anyone else.
git_() { git -c safe.directory="${repo}" -C "${repo}" "$@"; }

# Serialize concurrent submissions updating the same checkout.
exec 9>"${VISTA_JOB_DIR}/.forge-checkout.lock"
if ! flock -w 600 9; then
    echo "[prepare_forge] WARNING: could not lock the checkout; updating without the lock" >&2
fi

if [ -d "${repo}/.git" ]; then
    git_ fetch -q --depth 1 origin "${FORGE_BRANCH}"
    # Pull the latest: the shared checkout carries no local work, so a forced
    # checkout of what was fetched is the whole update. Untracked build products
    # survive it.
    git_ checkout -q -f -B "${FORGE_BRANCH}" FETCH_HEAD
else
    rm -rf "${repo}.tmp"
    git clone -q --depth 1 --branch "${FORGE_BRANCH}" \
        --config core.sharedRepository=group \
        "${FORGE_REPO_URL}" "${repo}.tmp"
    mv "${repo}.tmp" "${repo}"
fi
echo "[prepare_forge] forge ${FORGE_BRANCH} at $(git_ log -1 --format='%h %cd %s' --date=short)"

for f in train/deepy.py train/train.py train/configs/lux.yml; do
    [ -f "${repo}/${f}" ] || { echo "[prepare_forge] ERROR: ${repo}/${f} missing after checkout" >&2; exit 1; }
done

for f in all_text_document.bin all_text_document.idx all_vocab.json; do
    [ -r "${FORGE_DATA_DIR}/${f}" ] || { echo "[prepare_forge] ERROR: ${FORGE_DATA_DIR}/${f} not readable" >&2; exit 1; }
done

# Training reads the corpus through symlinks in a project-writable dir, because
# NeoX writes its dataset index maps (<prefix>_train_indexmap_<N>ns_*.npy) NEXT
# TO the data prefix on first use -- and FORGE_DATA_DIR is read-only to everyone
# but its owner. Here they are cached for the whole project, and a later run of
# the same shape (same N = iterations x global batch) reuses them.
data_links="${VISTA_JOB_DIR}/data"
mkdir -p "${data_links}"
for f in all_text_document.bin all_text_document.idx all_vocab.json; do
    ln -sfn "${FORGE_DATA_DIR}/${f}" "${data_links}/${f}"
done
echo "[prepare_forge] data OK (index maps cached in ${data_links})"
