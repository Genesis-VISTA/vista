# HPC jobs

The agent can submit jobs from VISTA's own catalog, `hpc_jobs/`, and jobs that a project's
loaded skills ship (see [Self-contained HPC skills](#self-contained-hpc-skills)). This guide is
for adding and maintaining those jobs. How submission reaches each cluster, and what researchers connect,
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
    ├── setup_odo.sh           # optional; runs inside the job, before the job script
    ├── setup_frontier.sh      # optional; same, for Frontier
    ├── setup_perlmutter.sh    # optional; same, for Perlmutter
    ├── setup_lux.sh           # optional; same, for Lux
    └── ...                    # other supporting files and folders, uploaded to the job's src/ folder
```

A job needs at least one cluster's script to load at all. Submitting it to a cluster then needs
both that cluster's script and its section in `cluster_defaults.json`: without the section VISTA
refuses the submission, even if every field in it would be a default (`{"odo": {}}` is enough). The README, the defaults and the
job and setup scripts at the top of the folder are read by VISTA and not uploaded; everything
else in the folder is, subfolders included, at the same relative paths under `$RUN_DIR_<Cluster>`
(a `README.md` in a subfolder is uploaded like any other file). Names starting with `.`,
`__pycache__` folders and symlinks are never uploaded. A submission re-sends only files that are
missing on the cluster or have a different size.
[`hpc_jobs/example/`](../hpc_jobs/example/) and the bundled hello skills
([`hello-odo`](../backend/src/vista_backend/db/skills/hello-odo/hpc_jobs/hello/),
[`hello-frontier`](../backend/src/vista_backend/db/skills/hello-frontier/hpc_jobs/hello/) and
[`hello-lux`](../backend/src/vista_backend/db/skills/hello-lux/hpc_jobs/hello/))
are small working examples.

## job.\<cluster\>.slurm

A standard Slurm batch script, inlined into the IRI JobSpec on every cluster. Its `#SBATCH`
lines are ignored: the job's resources come from `cluster_defaults.json`. It runs as
`bash -l -c`, so errexit is off unless the script turns it on. The agent can pass arguments to
the job, which the script reads as `$1`, `$2`, ...

The job's environment carries:

| Variable | Clusters | What it is |
|---|---|---|
| `VISTA_OUT` | all | The job's own output directory, already created. Save outputs and logs under it so VISTA can fetch them. |
| `RUN_DIR_<Cluster>` (`RUN_DIR_Odo`, `RUN_DIR_Frontier`, `RUN_DIR_Perlmutter`, `RUN_DIR_Lux`) | all | The uploaded source folder |
| `FORGE_MODEL_<Cluster>` | all | A `model` folder inside the job's shared directory |
| `VISTA_JOB_DIR` | all | State the job's runs share, such as a downloaded model |

On Odo the script also starts with its working directory set to the source folder. Where these
folders are on each cluster is in [hpc.md](hpc.md#remote-directories).

## Setup scripts

Optional. VISTA runs it inside the job, on its first node, before the job script, in a login
`bash` of its own. On the OLCF clusters it already has `VISTA_OUT`, `HOME` and the OLCF proxy,
so it can clone or update code; it also has the job's other variables and `iri.environment`. A
non-zero exit ends the job, with `VISTA: setup_<cluster>.sh failed with exit code <n>` in its
stderr. What it exports does not reach the job script. (`iri.pre_launch`, which once carried it,
is refused.)

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
- `resources.gpus_per_node` is sent to IRI as GPUs per process, so it must be a multiple of
  `processes_per_node` (default 1).
- Lux only: `iri.partition` is required, and is sent as IRI's `queue_name`, which Lux takes as
  the Slurm partition (there is no QOS). `resources.gpus_per_node` is required too: without it
  the job gets no GPU. Because Lux hands GPUs out per task, a step's tasks each see only their
  own GPUs, as device 0: code that picks its GPU by local rank needs `SLURM_GPU_BIND=none` (or
  `srun --gpu-bind=none`), as `forge-pretrain` sets. A step with more tasks per node than
  `processes_per_node` needs `--gpus-per-task`, and one without `--cpus-per-task` gets one CPU
  per task (`cpu_cores_per_process` does not reach the job).

## Self-contained HPC skills

A skill can ship its own jobs, so a new cluster workflow needs no change to VISTA. Put each job
in `hpc_jobs/<job>/` inside the skill, in exactly the format above. When a project loads the
skill, the agent submits it as `<skill>/<job>`:

```
my-skill/
├── SKILL.md                   # tells the agent the job exists, its script_args, how to read outputs
└── hpc_jobs/
    └── my-job/
        ├── README.md          # starts with "# my-job" (the folder name, not "my-skill/my-job")
        ├── cluster_defaults.json
        ├── job.lux.slurm
        ├── setup_lux.sh       # optional
        └── configs/model.yaml # nested files are uploaded too
```

```text
submit_hpc_job(job="my-skill/my-job", cluster="lux", script_args="CONFIG_NAME=model.yaml")
```

VISTA looks the name up on every call: its own catalog first, then the calling project's loaded
skills. A skill the project has not loaded provides no jobs. The sandbox sees the loaded skills read-only at
`/mnt/skills`, so the agent cannot add a job to one; a new or changed skill reaches the project
only by an import. A skill's jobs are checked with the
same rules as the catalog's (a job script, the README heading, a `cluster_defaults.json` that
parses), when they are submitted; a broken job fails only its own submissions, with a message
naming the rule. The `submit_hpc_job` description lists only the built-in jobs, so the skill's
SKILL.md must name its jobs and their arguments; an unknown job name lists every job the
project's skills ship.

On the cluster a skill job is laid out like any other, with `<skill>/<job>` as its name,
except that its sources folder is named for their contents:
sources in `<remote_dir>.<user>.jobs/<skill>/<job>/src-<hash>/` and `VISTA_JOB_DIR` in
`<remote_dir>.out/<skill>/<job>` (see [hpc.md](hpc.md#remote-directories)). An upload skips a
file already there at the same size, and re-importing a skill with Replace makes in-place edits
routine, so a same-length fix would otherwise never reach the cluster. Each version of the
sources gets its own folder instead; old ones are left on the cluster. Read sources through
`$RUN_DIR_<Cluster>`, never by that path.

What to keep in mind when writing one:

- **Flat or nested files.** Both work. Keep configs flat when the job copies them into another
  tool's config folder with a glob, as `primus-smoke` does; use subfolders when the tool reads
  a tree. Ship sources and configs only: each file is one upload on every first submission, so
  build images and download models on the cluster instead.
- **Setup in the job.** `setup_<cluster>.sh` runs at the start of every run (see
  [Setup scripts](#setup-scripts)). Make it idempotent, and keep shared state (a checkout, a build)
  under `VISTA_JOB_DIR`, so only the first run pays for it. Take a lock (`flock`) there, since
  two runs can start together. A non-zero exit ends the job with the reason in its log.
- **`script_args` as a `KEY=VALUE` allow-list.** VISTA splits `script_args` like a shell and
  hands the words to the job script as `$@`. Accept only the keys the README lists and refuse
  anything else, so an argument the agent invents fails at once rather than being ignored:

  ```bash
  for kv in "$@"; do
    case "$kv" in
      CONFIG_NAME=*|GLOBAL_BATCH_SIZE=*|TRAIN_ITERS=*) export "${kv?}" ;;
      *) echo "ERROR: unknown argument '$kv' (allowed: CONFIG_NAME, GLOBAL_BATCH_SIZE, TRAIN_ITERS)" >&2; exit 2 ;;
    esac
  done
  ```

- **Write only under `$VISTA_OUT` and `$VISTA_JOB_DIR`.** The sources folder is read-only to the
  job on the OLCF clusters, where it runs as the project's automation user.

To have the chat agent write one, load the bundled `hpc-skill-author` skill: it has templates
and a validator for exactly this contract, and the result is imported from the project output in
one click ([skill-onboarding.md](skill-onboarding.md#4-have-the-agent-write-it-hpc-skills)).
The `lux-primus-skill` repository (`primus-on-lux/primus-smoke`, AMD Primus training on Lux)
is the worked example. Who can see that a skill runs cluster jobs is in
[skill-onboarding.md](skill-onboarding.md#hpc-jobs-inside-a-skill).

## Testing a job

`mcp_servers/vista_mcp_server/tests/test_job_catalog.py` checks every job in the catalog
hermetically: the README's heading, at least one cluster script, that the catalog loads, and
that Lux jobs name a partition and GPUs. It checks the jobs inside the skills VISTA ships
(`backend/src/vista_backend/db/skills/*/hpc_jobs/`) the same way.
`tests/test_skill_jobs.py` covers looking skill jobs up and uploading nested files.
Run it with `cd mcp_servers/vista_mcp_server && uv run --extra dev pytest tests/test_job_catalog.py`.
Submitting to a real cluster is part of the opt-in [validation lane](validation-lane.md).
