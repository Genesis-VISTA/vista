#!/bin/bash
# Runs inside every Lux job, on its first node, before job.lux.slurm: in a login
# shell of its own, with the proxy, VISTA_OUT, RUN_DIR_Lux, VISTA_JOB_DIR and
# iri.environment exported. A non-zero exit ends the job; its exports do not
# reach the job script. Keep it idempotent: only the first run should pay for a
# clone or a build. Delete this file if the job needs no setup.
set -euo pipefail
: "${VISTA_JOB_DIR:?VISTA_JOB_DIR not set}"
mkdir -p "$VISTA_JOB_DIR"

# TODO, e.g. clone or update a repository, under a lock so two runs starting
# together cannot race:
# exec 9>"$VISTA_JOB_DIR/.setup.lock"; flock 9
# [ -d "$VISTA_JOB_DIR/src/.git" ] || git clone https://example.org/repo.git "$VISTA_JOB_DIR/src"
# git -C "$VISTA_JOB_DIR/src" pull --ff-only
