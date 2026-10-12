---
name: hpc-skill-author
description: >-
  Write a new VISTA skill that ships its own HPC job (Slurm script, cluster
  defaults, setup script, configs) for Lux, Frontier, Odo or Perlmutter, check
  it with the bundled validator, and hand it to the researcher to import. Use
  when the user asks to make, create or package an HPC skill, a cluster job, or
  a reusable workflow that runs on a cluster.
metadata:
  tags: ["HPC", "Skills", "Slurm", "Lux", "Frontier", "Odo", "Perlmutter"]
---

# Authoring an HPC skill

A skill can carry its own cluster jobs at `<skill>/hpc_jobs/<job>/`. Once a
project loads the skill, `submit_hpc_job(job="<skill>/<job>", cluster=...)` runs
it; nothing is added to VISTA. You write the skill in the sandbox, validate it,
and the researcher imports it with one click.

## Workflow

Progress:
- [ ] 1. Agree on the job: cluster(s), what it runs, inputs, outputs, run time.
- [ ] 2. Copy the templates to `/mnt/data/output/<skill>/` and fill them in.
- [ ] 3. Run the validator until it reports no errors; fix warnings unless there is a reason not to.
- [ ] 4. Hand off: tell the researcher how to import it.

### 1. Agree on the job

Ask only what you cannot infer: which cluster, what software (a module, a
container image, a repository to clone), what the researcher wants to vary per
run (these become `script_args` keys), what the job writes, and roughly how long
it runs. Prefer one small job that works end to end over a general one.

### 2. Write the skill

Pick a kebab-case skill name, e.g. `lammps-on-frontier`, and a job name, e.g.
`melt-run`. The skill folder name MUST equal the SKILL.md `name`: it is the
`<skill>` in `<skill>/<job>`.

```bash
SKILL=lammps-on-frontier JOB=melt-run
T=/mnt/skills/hpc-skill-author/templates
mkdir -p /mnt/data/output/$SKILL/hpc_jobs
cp $T/skill/SKILL.md /mnt/data/output/$SKILL/SKILL.md
cp -r $T/job /mnt/data/output/$SKILL/hpc_jobs/$JOB
grep -rl '__SKILL__\|__JOB__' /mnt/data/output/$SKILL | xargs sed -i "s/__SKILL__/$SKILL/g; s/__JOB__/$JOB/g"
```

The templates are a Lux job. For another cluster rename `job.lux.slurm` and
`setup_lux.sh`, and replace the `lux` section of `cluster_defaults.json` (see
below). Then fill in every `TODO`.

```
<skill>/
├── SKILL.md                 # when to use it, the job name, its script_args, how to read outputs
└── hpc_jobs/
    └── <job>/
        ├── README.md        # FIRST LINE exactly "# <job>"; clusters, defaults, script args, outputs
        ├── cluster_defaults.json   # one section per cluster the job runs on
        ├── job.<cluster>.slurm     # one per cluster: odo, frontier, perlmutter, lux
        ├── setup_<cluster>.sh      # optional
        └── ...                     # configs and small scripts; subfolders are fine
```

`README.md`, `cluster_defaults.json` and the job and setup scripts at the top of
the job folder are read by VISTA and not uploaded; everything else is uploaded
to `$RUN_DIR_<Cluster>` at the same relative path. Names starting with `.`,
`__pycache__` and symlinks are never uploaded. Ship sources and configs only;
download models and build images on the cluster.

#### Environment the job script gets

| Variable | Clusters | Use |
|---|---|---|
| `VISTA_OUT` | all | This run's output folder, already created. Write outputs and logs here. |
| `RUN_DIR_<Cluster>` (`RUN_DIR_Lux`, `RUN_DIR_Frontier`, `RUN_DIR_Odo`, `RUN_DIR_Perlmutter`) | all | The uploaded job folder. Read only. |
| `VISTA_JOB_DIR` | Lux, Frontier | State shared by all runs of this job (a checkout, a build, a model). |

`script_args` is split like a shell command and passed as `$1 $2 ...`. Accept
only `KEY=VALUE` pairs from an allow-list and refuse anything else (the
template does this), so an argument nobody planned for fails at once.

Write only under `$VISTA_OUT` and `$VISTA_JOB_DIR`. On Odo and Frontier the job
runs as the project's automation user, which can only read the uploaded
sources: put virtual environments under `$VISTA_OUT`, and `cd "$VISTA_OUT"`
before running tools that write to the working directory.

#### cluster_defaults.json

A section per cluster, and a section for every `job.<cluster>.slurm` (VISTA
refuses to submit to a cluster without one; `{"odo": {}}` is enough). All
fields are optional:

```json
{
  "lux":        {"duration": 1800, "resources": {"node_count": 1, "gpus_per_node": 8, "exclusive_node_use": true},
                 "iri": {"partition": "batch", "environment": {"MY_VAR": "value"}}},
  "frontier":   {"duration": 1800, "resources": {"node_count": 1}, "iri": {"queue_name": "batch"}},
  "odo":        {"duration": 600,  "resources": {"node_count": 1}, "iri": {"queue_name": "batch"}},
  "perlmutter": {"duration": 1800, "resources": {"node_count": 1}, "iri": {"queue_name": "regular", "constraint": "gpu"}}
}
```

- `duration` is in seconds (default 1800; at most 4 hours).
- Lux needs `iri.partition` and `resources.gpus_per_node`; `iri.queue_name`
  there is the QOS.
- Perlmutter needs `iri.constraint` (`"gpu"` or `"cpu"`); `iri.image` and
  `iri.module` select a container and modules.
- `iri.environment` values are strings, added to the job's environment.

#### Setup scripts

- `setup_lux.sh` runs on the Lux **login node** before every `sbatch`, with the
  proxy, `RUN_DIR_Lux`, `VISTA_JOB_DIR` and `iri.environment` exported. Clone or
  update code there, into `$VISTA_JOB_DIR`, idempotently (`flock` if two
  submissions could race). A non-zero exit fails the submission with its output.
- `setup_odo.sh`, `setup_frontier.sh`, `setup_perlmutter.sh` run on the compute
  node before the job script, as part of the job.

#### SKILL.md of the new skill

`submit_hpc_job` lists only VISTA's built-in jobs, so the new SKILL.md must name
each job as `<skill>/<job>`, its clusters, every `script_args` key, a submit
example, and which files in the output folder to read
(`get_hpc_job_outputs`). Keep the `name` equal to the folder name.

### 3. Validate

```bash
python3 /mnt/skills/hpc-skill-author/scripts/validate.py /mnt/data/output/<skill>
```

It applies the checks VISTA applies when the skill is imported and when a job
is submitted, and exits 1 on any `ERROR`. `WARNING`s are contract advice
VISTA cannot check for you. Fix and rerun until it prints `OK`. Run `bash -n`
on each script too. Use `--job <job folder>` to check one job alone.

### 4. Hand off

Tell the researcher, in these words or close to them:

> The skill is in your project's output folder as `<skill>/`. To use it, open
> **Skills → Import → From project output**, choose `<skill>` and import it. It
> is imported private and loaded into this project; its badge says which HPC
> jobs it can run. Then ask me to run `<skill>/<job>` on `<cluster>`.

You cannot import or load it yourself. After they import it, the job is
available from their next message. If they ask for changes, edit the files in
`/mnt/data/output/<skill>/`, validate again, and ask them to import it again
and choose **Replace**.

Do not submit the job before it is imported; do not copy it into any other
folder.
