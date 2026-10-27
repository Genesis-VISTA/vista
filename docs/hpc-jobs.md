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
    ├── setup_odo.sh           # optional; pre_launch setup, inlined into the JobSpec
    ├── setup_frontier.sh      # optional; same, for Frontier
    ├── setup_perlmutter.sh    # optional; same, for Perlmutter
    ├── setup_lux.sh           # optional; runs on the Lux login node before sbatch
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

## Constraint and reservation

`submit_hpc_job` takes two optional arguments per submission:

- `constraint` is a Slurm node constraint, such as `gpu&hbm80g`. It **replaces** the job's
  default `iri.constraint`, never combines with it: to keep the default, restate it.
- `reservation` names a Slurm reservation. It comes only from the submission; it is not a
  job default, so `cluster_defaults.json` has no field for it.

Both are refused before anything is submitted, a dry run included, if they hold anything
but letters, digits and `_ . : -` (a constraint may also use `, * & | [ ]`).

| Cluster | How they are sent |
|---|---|
| Lux | `#SBATCH -C <constraint>` and `#SBATCH --reservation=<name>` |
| Perlmutter, Odo, Frontier | over IRI, as `custom_attributes.constraint` and `reservation_id`, only when set |

On Odo and Frontier VISTA then reads Slurm's record of the job once. If Slurm did not record
a value that was requested, the job is cancelled and the tool fails, saying OLCF did not pass
it to Slurm and naming Perlmutter and Lux. If the record cannot be read, the job is kept and
the summary says `verified: no (<reason>)`. OLCF honours reservations but currently drops
constraints, so a constrained submit to Odo or Frontier fails today; use Perlmutter or Lux.
The summary adds `constraint:`, `reservation:` and `verified:` lines where they apply.

A campaign's `dispatch_cycle` takes the same two arguments and applies them to every job in
that cycle.

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
- **Setup on the Lux login node.** `setup_lux.sh` runs before every `sbatch`, on the login node,
  with the proxy, `RUN_DIR_Lux`, `VISTA_JOB_DIR` and `iri.environment` exported. Make it
  idempotent, and keep shared state (a checkout, a build) under `VISTA_JOB_DIR`, so only the
  first submission pays for it. Take a lock (`flock`) if two submissions could race. A non-zero
  exit fails the submit call with the script's last 40 lines.
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
  job on Odo and Frontier, where it runs as the project's automation user.

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
