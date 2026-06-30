#!/bin/bash -l
# Frontier pre_launch validation for vit-train. Runs ONCE on the head compute node before
# job.frontier.slurm (inlined into the IRI JobSpec pre_launch by vista's dispatcher). Fails
# fast — before the GPU allocation does any work — if its prerequisites are missing.
#
# Env provided by vista's IRI dispatcher (submit_job_mcp.py) + cluster_defaults.json:
#   RUN_DIR_Frontier  where vista Globus-staged run_state_point.py.
#   CLIMATEVIT_ENV    (optional) conda env prefix to activate instead of the module stack.

set -euo pipefail

: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"

echo "[setup_frontier] RUN_DIR_Frontier=${RUN_DIR_Frontier}"

if [ ! -f "${RUN_DIR_Frontier}/run_state_point.py" ]; then
    echo "[setup_frontier] ERROR: run_state_point.py missing in ${RUN_DIR_Frontier}" >&2
    echo "[setup_frontier] vista should have staged it via Globus; verify the upload + dir permissions." >&2
    exit 1
fi

# Either a conda env prefix or the GPU module stack must be resolvable.
if [ -n "${CLIMATEVIT_ENV:-}" ]; then
    if [ ! -d "${CLIMATEVIT_ENV}" ]; then
        echo "[setup_frontier] ERROR: CLIMATEVIT_ENV set but not found at ${CLIMATEVIT_ENV}" >&2
        exit 1
    fi
    echo "[setup_frontier] OK: run_state_point.py present; conda env prefix ${CLIMATEVIT_ENV} exists"
else
    module use "${CLIMATEVIT_MODULEPATH:-/sw/aaims/crusher/modulefiles}" 2>/dev/null || true
    if ! module avail "${CLIMATEVIT_MODULE:-xforge}" 2>&1 | grep -q "${CLIMATEVIT_MODULE:-xforge}"; then
        echo "[setup_frontier] WARNING: module ${CLIMATEVIT_MODULE:-xforge} not found on "
        echo "[setup_frontier] ${CLIMATEVIT_MODULEPATH:-/sw/aaims/crusher/modulefiles}; set CLIMATEVIT_MODULE/"
        echo "[setup_frontier] CLIMATEVIT_MODULEPATH or CLIMATEVIT_ENV in cluster_defaults.json." >&2
    fi
    echo "[setup_frontier] OK: run_state_point.py present"
fi
