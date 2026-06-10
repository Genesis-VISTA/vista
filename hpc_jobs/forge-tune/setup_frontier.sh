#!/bin/bash -l
# Frontier pre_launch setup for forge-tune. Runs ONCE per submission on the head
# compute node, before the per-rank job.frontier.slurm body starts. Injected into
# the IRI JobSpec by vista's submit_job_mcp dispatcher.
#
# Responsibilities:
#   1. Validate the forge-tune source files vista uploaded via SCP.
#   2. Validate the pre-downloaded FORGE model weights are reachable.
#
# Env vars provided by vista's IRI dispatcher (submit_job_mcp.py), derived from
# the user's frontier_remote_dir + cluster_defaults.json:
#   RUN_DIR_Frontier      <remote_dir>/<job>/src    (vista uploads here via scp)
#   FORGE_MODEL_Frontier  path to pre-downloaded FORGE weights (from cluster_defaults.json
#                         iri.environment; world-shared, no per-submission download needed).

set -euo pipefail

: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"
: "${FORGE_MODEL_Frontier:?FORGE_MODEL_Frontier not set}"

echo "[setup_frontier] RUN_DIR_Frontier=${RUN_DIR_Frontier}"
echo "[setup_frontier] FORGE_MODEL_Frontier=${FORGE_MODEL_Frontier}"

# 1. Validate source files vista scp'd to RUN_DIR_Frontier.
required=(forge-tune.py hybrid_split.py setup_dist_vars.sh Molten_Salt_Thermophysical_Properties.csv)
for f in "${required[@]}"; do
    if [ ! -f "${RUN_DIR_Frontier}/${f}" ]; then
        echo "[setup_frontier] ERROR: missing ${RUN_DIR_Frontier}/${f}" >&2
        echo "[setup_frontier] vista should have uploaded these via scp; verify VISTA_MCP_FRONTIER_SSH_* settings, the user's frontier_remote_dir, and dir permissions (the IRI service runs as <project>_auser and needs group-readable sources)" >&2
        exit 1
    fi
done
echo "[setup_frontier] forge-tune sources OK"

# 2. Validate FORGE model weights exist + are readable. World-shared at OLCF — no download.
if [ ! -d "${FORGE_MODEL_Frontier}" ]; then
    echo "[setup_frontier] ERROR: FORGE_MODEL_Frontier=${FORGE_MODEL_Frontier} is not a directory" >&2
    exit 1
fi
echo "[setup_frontier] FORGE model dir OK"
ls -la "${FORGE_MODEL_Frontier}" | head -10
