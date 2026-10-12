---
name: __SKILL__
description: >-
  TODO: what this skill runs, on which cluster, and when to use it (the agent
  reads this to decide whether to load the skill's instructions).
metadata:
  tags: ["HPC", "TODO"]
---

# TODO: title

TODO: one paragraph on what the job does and what a run produces.

## Running it

The job is `__SKILL__/__JOB__`. It runs on Lux (TODO: list the clusters).

```text
submit_hpc_job(job="__SKILL__/__JOB__", cluster="lux", script_args="STEPS=100")
get_hpc_job_status(job_id=<id>, cluster="lux")
get_hpc_job_outputs(job_id=<id>, cluster="lux")
```

`script_args` is a space-separated list of `KEY=VALUE` pairs; any other key is
refused:

- `STEPS=<n>`: TODO (default 10).

Confirm the cluster, node count and duration with the user before submitting.

## Outputs

In the job's output folder:

- `summary.txt`: TODO.
- `run.log`: the full log.

TODO: how to read the results and what a healthy run looks like.
