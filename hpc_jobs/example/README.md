# example

A simple test job that outputs an example chart.

Default nodes: 1
Default time: 0:02:00

## Script args
Arbitrary arguments allowed and are printed

## Real-cluster smoke (validation lane)

Opt-in only — never part of hermetic PR CI. Full procedure:
[`docs/validation-lane.md`](../../docs/validation-lane.md).

```bash
# MCP: leave VISTA_MCP_HPC_DRY_RUN unset
# Configure S3M / IRI tokens in UI → User settings (or deployment secrets)
export VISTA_RUN_HPC=1
export VISTA_HPC_SMOKE_CLUSTER=odo   # or frontier
cd backend
uv run pytest tests/live/test_hpc_example_smoke.py -v -m hpc
```

Agent / tool path:

```text
submit_hpc_job(job="example", cluster="odo")
```

Expected: a real job id (not `dry-…`), a terminal Slurm-like state, and
optionally fetchable outputs via `get_hpc_job_outputs` (cluster-dependent).
