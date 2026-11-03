#!/bin/bash -l
# hello-frontier/hello on Frontier. VISTA's IRI dispatcher puts this script after a short setup (proxy, HOME, `module purge`):
#   VISTA_OUT      this run's output folder (already created); write outputs here
#   RUN_DIR_Frontier  this job folder's uploaded files (input.txt); read only
#   VISTA_JOB_DIR     shared state of every run of this job; this job needs none
# Takes no args.
set -euo pipefail
: "${VISTA_OUT:?VISTA_OUT not set}"
: "${RUN_DIR_Frontier:?RUN_DIR_Frontier not set}"

if [ "$#" -gt 0 ]; then
  echo "ERROR: hello takes no arguments, got $# argument(s)" >&2
  exit 2
fi

INPUT="$RUN_DIR_Frontier/input.txt"
[ -r "$INPUT" ] || { echo "ERROR: $INPUT was not uploaded" >&2; exit 3; }

cd "$VISTA_OUT"
report() {
  echo "cluster: Frontier"
  echo "host: $(hostname)"
  echo "slurm job: ${SLURM_JOB_ID:-unknown}"
  echo "nodes: ${SLURM_JOB_NODELIST:-unknown}"
  echo "start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  module load rocm 2>/dev/null || true
  echo "--- module list"
  command -v module >/dev/null 2>&1 && module list 2>&1 || echo "module: not available"
  echo "--- rocm-smi"
  if command -v rocm-smi >/dev/null 2>&1; then
    rocm-smi --showproductname || echo "rocm-smi failed (exit $?)"
  else
    echo "rocm-smi not found"
  fi
  echo "--- input.txt"
  cat "$INPUT"
  echo "input_sha256: $(sha256sum "$INPUT" | cut -d' ' -f1)"
  echo "end: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
report 2>&1 | tee "$VISTA_OUT/hello.txt" | tee "$VISTA_OUT/run.log"

grep -E '^(cluster|host|nodes|start|end|input_sha256):' "$VISTA_OUT/run.log" > "$VISTA_OUT/summary.txt"
