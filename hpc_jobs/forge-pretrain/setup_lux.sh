#!/bin/bash -l
# Lux setup for forge-pretrain. Runs on the Lux LOGIN node (over SSH, as the user)
# before sbatch -- compute nodes can't clone, and a failure here fails the submit
# call instead of a queued job.
#
# Env vars exported by vista's Lux dispatcher (submit_job_mcp._submit_lux_job):
#   RUN_DIR_Lux      $VISTA_JOB_DIR/src, already holding the uploaded sources
#   VISTA_JOB_DIR    <remote_dir>/forge-pretrain
#   http(s)_proxy    OLCF proxy (the login node has no direct outbound network)
#   FORGE_*, LUX_ENV_SCRIPT   from cluster_defaults.json -> lux.iri.environment

set -euo pipefail

: "${RUN_DIR_Lux:?RUN_DIR_Lux not set}"
: "${LUX_ENV_SCRIPT:?LUX_ENV_SCRIPT not set; add to cluster_defaults.json}"

[ -r "${LUX_ENV_SCRIPT}" ] || { echo "[setup_lux] ERROR: ${LUX_ENV_SCRIPT} not readable" >&2; exit 1; }

bash "${RUN_DIR_Lux}/prepare_forge.sh"
