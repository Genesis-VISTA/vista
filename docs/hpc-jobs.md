# HPC jobs

The agent can only submit jobs from a predefined catalog, `hpc_jobs/`. This guide is for adding
and maintaining those jobs. How submission reaches each cluster, and what researchers connect,
is in [hpc.md](hpc.md); generating a job alongside a skill is covered in
[skill-onboarding.md](skill-onboarding.md).

## Directory layout

Each job lives in its own subdirectory and needs a `README.md` plus, for each cluster it runs
on, a job script and a section in `cluster_defaults.json`:

```
hpc_jobs/
└── my-job/
    ├── README.md              # required; starts with "# my-job"; shown to the agent as the job's description
    ├── cluster_defaults.json  # one section per cluster the job runs on
    ├── job.odo.slurm          # Slurm batch script for Odo (OLCF, open enclave)
    ├── job.frontier.slurm     # Slurm batch script for Frontier (OLCF, moderate enclave)
    ├── job.perlmutter.slurm   # Slurm batch script for Perlmutter (NERSC)
    ├── job.lux.slurm          # Slurm batch script for Lux (OLCF)
    ├── setup_odo.sh           # optional; pre_launch setup, inlined into the JobSpec
    ├── setup_frontier.sh      # optional; same, for Frontier
    ├── setup_perlmutter.sh    # optional; same, for Perlmutter
    ├── setup_lux.sh           # optional; runs on the Lux login node before sbatch
    └── ...                    # other supporting files, uploaded to the job's src/ folder
```

A job needs at least one cluster's script to load at all. Submitting it to a cluster then needs
both that cluster's script and its section in `cluster_defaults.json`: without the section VISTA
refuses the submission, even if every field in it would be a default (`{"odo": {}}` is enough). The README, the defaults and the
job and setup scripts are read by VISTA and not uploaded; everything else in the folder is.
[`hpc_jobs/example/`](../hpc_jobs/example/) and [`hpc_jobs/lux-hello/`](../hpc_jobs/lux-hello/)
are small working examples.

## job.\<cluster\>.slurm

A standard Slurm batch script. On Odo, Frontier and Perlmutter it is inlined into the IRI
JobSpec; on Lux it is sent to `sbatch` on stdin, with the job's resources as `#SBATCH`
directives. The agent can pass arguments to the job, which the script reads as `$1`, `$2`, ...

The job's environment carries:

| Variable | Clusters | What it is |
|---|---|---|
| `VISTA_OUT` | all | The job's own output directory, already created. Save outputs and logs under it so VISTA can fetch them. |
| `RUN_DIR_<Cluster>` (`RUN_DIR_Odo`, `RUN_DIR_Frontier`, `RUN_DIR_Perlmutter`, `RUN_DIR_Lux`) | all | The uploaded source folder |
| `FORGE_MODEL_<Cluster>` | Odo, Frontier, Perlmutter | A `model` folder inside the job's shared directory |
| `VISTA_JOB_DIR` | Frontier, Lux | State the job's runs share, such as a downloaded model |

On Odo the script also starts with its working directory set to the source folder. Where these
folders are on each cluster is in [hpc.md](hpc.md#remote-directories).

`setup_lux.sh` differs from the other setup scripts: it runs on the Lux login node before
`sbatch`, where the network is reachable through the proxy, so it is the place to clone or
update code. A failure there is a tool error at submit time, not a failed job later.

## cluster_defaults.json

Per-cluster submission defaults: one section per cluster the job runs on (`odo`, `frontier`,
`perlmutter`, `lux`). Every field inside a section is optional:

```json
{
  "odo": {
    "duration": 120,
    "resources": {
      "node_count": 1,
      "process_count": null,
      "processes_per_node": null,
      "cpu_cores_per_process": null,
      "exclusive_node_use": true
    },
    "iri": {
      "queue_name": "batch",
      "constraint": null,
      "image": null,
      "module": null,
      "environment": {}
    }
  }
}
```

- `duration` is in **seconds** (default 1800).
- `iri.environment` entries are merged into the job's environment and win over the variables
  above.
- `iri.constraint` is a Slurm constraint, such as `"gpu"` on Perlmutter.
- Lux only: `iri.partition` is required by Lux (`#SBATCH -p`), `iri.queue_name` is the QOS
  (`-q`), and `resources.gpus_per_node` becomes `--gpus-per-node`. The IRI clusters choose the
  partition and bind GPUs themselves.

## Testing a job

`mcp_servers/vista_mcp_server/tests/test_job_catalog.py` checks every job in the catalog
hermetically: the README's heading, at least one cluster script, that the catalog loads, and
that Lux jobs name a partition and GPUs.
Run it with `cd mcp_servers/vista_mcp_server && uv run --extra dev pytest tests/test_job_catalog.py`.
Submitting to a real cluster is part of the opt-in [validation lane](validation-lane.md).
