# __JOB__

TODO: one paragraph on what the job runs. It wraps __REPO_URL__ at commit
`__REPO_COMMIT__`, cloned on the cluster, not copied into the skill. Part of the
`__SKILL__` skill.

Supported clusters: Lux (TODO).

Default nodes: 1
Default time: 0:30:00 (the first submission also clones the repository on the
Lux login node; later ones reuse the clone)

## Script args

`script_args` is a space-separated list of `KEY=VALUE` pairs. All are optional,
and any other key is refused:

- `STEPS=<n>`: TODO (default 10).

## Outputs

- `summary.txt`: TODO.
- `run.log`: the full log, starting with the repository and commit it ran.
