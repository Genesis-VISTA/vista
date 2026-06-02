#!/usr/bin/env bash
# One-time accounting-db registration for the SciAgentBench SLURM
# substrate. Run after the first `docker compose up -d`.
set -euo pipefail

CTLD=sciagentbench-slurmctld

echo "Registering cluster 'linux' with the accounting db..."
docker exec "$CTLD" bash -lc "sacctmgr --immediate add cluster name=linux" || true

echo "Restarting slurmdbd + slurmctld to pick up the registration..."
docker compose restart sciagentbench-slurmdbd sciagentbench-slurmctld

echo "Done. Check with: docker exec $CTLD sinfo"
