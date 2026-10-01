#!/bin/bash -l
# Perlmutter pre_launch setup for forge-tune. Runs on the compute node in the native
# environment (before shifter starts), as injected into the IRI JobSpec by vista's
# submit_job_mcp dispatcher.
#
# Responsibilities:
#   1. Ensure $RUN_DIR_Perlmutter contains the forge-tune source files.
#   2. Ensure $FORGE_MODEL_Perlmutter contains the FORGE base model (downloaded from
#      a Dropbox folder share link the first time, then cached).
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py), derived from
# VISTA_MCP_NERSC_REMOTE_DIR / cluster_defaults.json:
#   RUN_DIR_Perlmutter       <remote_dir>.jobs/<job>/src  (user pre-populates with scp)
#   FORGE_MODEL_Perlmutter   <remote_dir>.out/<job>/model  (this script downloads here)
#   FORGE_MODEL_URL          model archive URL (from cluster_defaults.json)
# Either path can be overridden by adding it to iri.environment in cluster_defaults.json.

set -euo pipefail

: "${RUN_DIR_Perlmutter:?RUN_DIR_Perlmutter not set}"
: "${FORGE_MODEL_Perlmutter:?FORGE_MODEL_Perlmutter not set}"

FORGE_MODEL_URL="${FORGE_MODEL_URL:-https://www.dropbox.com/sh/byr1ydik5n1ucod/AADOu_9C6AwVPTThTUFQ7yQba?dl=1}"

echo "[setup_perlmutter] RUN_DIR_Perlmutter=${RUN_DIR_Perlmutter}"
echo "[setup_perlmutter] FORGE_MODEL_Perlmutter=${FORGE_MODEL_Perlmutter}"

# 1. Validate the forge-tune source files. Vista auto-uploads these via the IRI
#    Filesystem API on the first submit per job dir; the validation below is a
#    defensive check in case the upload was partial or skipped.
required=(forge-tune.py hybrid_split.py setup_dist_vars.sh Molten_Salt_Thermophysical_Properties.csv)
for f in "${required[@]}"; do
    if [ ! -f "${RUN_DIR_Perlmutter}/${f}" ]; then
        echo "[setup_perlmutter] ERROR: missing ${RUN_DIR_Perlmutter}/${f}" >&2
        echo "[setup_perlmutter] vista should have uploaded these; check VISTA_MCP_NERSC_REMOTE_DIR + IRI token validity, or scp manually as a fallback" >&2
        exit 1
    fi
done
echo "[setup_perlmutter] forge-tune sources OK"

# 2. Download + unpack the FORGE model on first use. Idempotent: skip if cached.
# Use a sentinel file so a partial download doesn't masquerade as a cache hit.
SENTINEL="${FORGE_MODEL_Perlmutter}/.vista_download_complete"
if [ -f "${SENTINEL}" ]; then
    echo "[setup_perlmutter] FORGE model already cached at ${FORGE_MODEL_Perlmutter}"
else
    echo "[setup_perlmutter] downloading FORGE model from ${FORGE_MODEL_URL%\?*}"
    mkdir -p "${FORGE_MODEL_Perlmutter}"
    tmp_zip="$(mktemp -p "$(dirname "${FORGE_MODEL_Perlmutter}")" forge-model.XXXXXX.zip)"
    trap 'rm -f "${tmp_zip}"' EXIT

    # Dropbox folder shares return a zip when ?dl=1. -L follows redirects.
    curl --fail --location --silent --show-error \
         --output "${tmp_zip}" \
         "${FORGE_MODEL_URL}"

    echo "[setup_perlmutter] unpacking $(du -h "${tmp_zip}" | cut -f1) to ${FORGE_MODEL_Perlmutter}"
    unzip -q -o "${tmp_zip}" -d "${FORGE_MODEL_Perlmutter}"

    touch "${SENTINEL}"
    echo "[setup_perlmutter] FORGE model ready"
fi

ls -la "${FORGE_MODEL_Perlmutter}" | head -20
