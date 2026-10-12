#!/bin/bash
# Runs on the Lux LOGIN node before every sbatch, with the proxy, RUN_DIR_Lux,
# VISTA_JOB_DIR and iri.environment exported. Keep it idempotent: only the
# first submission should pay for a clone or a build. Delete this file if the
# job needs no setup.
set -euo pipefail
: "${VISTA_JOB_DIR:?VISTA_JOB_DIR not set}"
mkdir -p "$VISTA_JOB_DIR"

# TODO, e.g. clone or update a repository, under a lock so two submissions
# cannot race:
# exec 9>"$VISTA_JOB_DIR/.setup.lock"; flock 9
# [ -d "$VISTA_JOB_DIR/src/.git" ] || git clone https://example.org/repo.git "$VISTA_JOB_DIR/src"
# git -C "$VISTA_JOB_DIR/src" pull --ff-only
