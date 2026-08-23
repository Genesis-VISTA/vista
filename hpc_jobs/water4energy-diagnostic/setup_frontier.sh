#!/bin/bash -l
# Frontier pre_launch validation for water4energy-diagnostic. Runs ONCE on the head
# compute node before job.frontier.slurm; vista inlines it into the IRI JobSpec as
# `attributes.pre_launch`. Purpose: fail in seconds with a readable message when a
# prerequisite is missing, instead of burning the walltime on a clone + venv build
# that cannot possibly succeed.
#
# Env available here comes from `attributes.environment` only — that means
# RUN_DIR_Frontier and the cluster_defaults.json entries. VISTA_OUT and the OLCF
# proxy are defined later, inside the job command itself, so do NOT reference them
# and do NOT do anything that needs the network.
#
#   W4E_DATA_DIR      read-only dir holding the pre-staged NetCDF climatologies.
#   RUN_DIR_Frontier  where vista staged run_diagnostic.py.

set -euo pipefail

: "${W4E_DATA_DIR:?W4E_DATA_DIR not set; add it to cluster_defaults.json iri.environment}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"

echo "[setup_frontier] W4E_DATA_DIR=${W4E_DATA_DIR}"
echo "[setup_frontier] RUN_DIR_Frontier=${RUN_DIR_Frontier}"

if [ ! -d "${W4E_DATA_DIR}" ]; then
    echo "[setup_frontier] ERROR: input data dir not readable: ${W4E_DATA_DIR}" >&2
    echo "[setup_frontier] It must be a directory readable by the account vista submits" >&2
    echo "[setup_frontier] Frontier jobs under. Fix W4E_DATA_DIR in cluster_defaults.json," >&2
    echo "[setup_frontier] or restore group read access on the staged climatologies." >&2
    exit 1
fi

# The two climatologies are gitignored upstream (1.1 GB + 153 MB) and must be
# pre-staged. The region polygon ships in the repo, so it is not checked here.
missing=0
for f in ERA5_ANN_198501_201412_climo.nc \
         v3.LR.historical_0101_ANN_198501_201412_climo.nc; do
    if [ ! -f "${W4E_DATA_DIR}/${f}" ]; then
        echo "[setup_frontier] ERROR: missing input: ${W4E_DATA_DIR}/${f}" >&2
        missing=1
    fi
done
if [ "${missing}" -ne 0 ]; then
    echo "[setup_frontier] The NetCDF climatologies are not in the git repo; they must be" >&2
    echo "[setup_frontier] staged on Lustre. See hpc_jobs/water4energy-diagnostic/README.md." >&2
    exit 1
fi

if [ ! -f "${RUN_DIR_Frontier}/run_diagnostic.py" ]; then
    echo "[setup_frontier] ERROR: run_diagnostic.py missing in ${RUN_DIR_Frontier}" >&2
    echo "[setup_frontier] vista should have staged it via Globus; verify the upload and" >&2
    echo "[setup_frontier] the directory permissions on <frontier_remote_dir>/<job>/src." >&2
    echo "[setup_frontier] Note the upload is skipped when src/ is already populated, so a" >&2
    echo "[setup_frontier] stale src dir also shows up here." >&2
    exit 1
fi

echo "[setup_frontier] OK: input climatologies + run_diagnostic.py present"
