# __JOB__

TODO: one paragraph on what the job does. Part of the `__SKILL__` skill.

Supported clusters: Odo, Frontier, Lux (TODO: delete the clusters this job does
not target: their `job.<cluster>.slurm`, their `setup_<cluster>.sh` and their
section of `cluster_defaults.json`, and name only the rest here).

Default nodes: 1
Default time: 0:30:00

## Script args

`script_args` is a space-separated list of `KEY=VALUE` pairs. All are optional,
and any other key is refused:

- `STEPS=<n>`: TODO (default 10).

## Outputs

- `summary.txt`: TODO.
- `run.log`: the full log.
