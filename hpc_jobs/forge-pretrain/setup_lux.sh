#!/bin/bash -l
# Lux setup for forge-pretrain. VISTA runs it inside the job, on the first node,
# before job.lux.slurm, as the project's automation user; a failure ends the job
# with the reason in its log.
#
# Env vars exported by vista (submit_job_mcp._submit_job):
#   RUN_DIR_Lux      <remote_dir>.<user>.jobs/forge-pretrain/src, already holding the uploaded sources
#   VISTA_JOB_DIR    <remote_dir>.out/forge-pretrain
#   http(s)_proxy    OLCF proxy (compute nodes have no direct outbound network)
#   FORGE_*, LUX_ENV_SCRIPT   from cluster_defaults.json -> lux.iri.environment

set -euo pipefail

: "${RUN_DIR_Lux:?RUN_DIR_Lux not set}"
: "${LUX_ENV_SCRIPT:?LUX_ENV_SCRIPT not set; add to cluster_defaults.json}"

[ -r "${LUX_ENV_SCRIPT}" ] || { echo "[setup_lux] ERROR: ${LUX_ENV_SCRIPT} not readable" >&2; exit 1; }

bash "${RUN_DIR_Lux}/prepare_forge.sh"
