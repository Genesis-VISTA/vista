---
name: hpc-skill-author
description: >-
  Write a new VISTA skill that ships its own HPC job (Slurm script, cluster
  defaults, setup script, configs) for Lux, Frontier, Odo or Perlmutter, from
  scratch or by wrapping existing code (a GitHub or GitLab repository or an uploaded
  folder or archive), check it with the bundled validator, and hand it to the
  researcher to import. Use when the user asks to make, create or package an
  HPC skill, a cluster job, or a reusable workflow that runs on a cluster, or
  to turn a repo or folder into one.
metadata:
  tags: ["HPC", "Skills", "Slurm", "Lux", "Frontier", "Odo", "Perlmutter"]
---

# Authoring an HPC skill

A skill can carry its own cluster jobs at `<skill>/hpc_jobs/<job>/`. Once a
project loads the skill, `submit_hpc_job(job="<skill>/<job>", cluster=...)` runs
it; nothing is added to VISTA. You write the skill in the sandbox, validate it,
and the researcher imports it with one click.

## Workflow

If the user points at existing code (a GitHub or GitLab URL, an uploaded folder or
archive, or a skill without `hpc_jobs/`), first follow
[Wrapping existing code](#wrapping-existing-code), then continue at step 3.

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

#### Software environment

**Prefer an environment that already exists.** Ask the user whether one is
already built on the cluster (a conda env, a venv, a container image, a
module). If so, the job only activates it: put its path in
`cluster_defaults.json` (`iri.environment`, e.g. `"CONDA_ENV": "/path/to/env"`)
and have the job script check it exists and fail with a clear message if not.
Recommend the user pre-build the environment once by hand when it is large or
fiddly; a job that builds its own spends queue time on it, and a failed build
costs a whole allocation.

**If the job must build a conda environment,** build it once into
`$VISTA_JOB_DIR` (shared by every run; on Odo use a path the user gives you),
under a lock, and mark it done so later runs skip straight to activating it.
`templates/conda-env/build_conda_env.sh` does exactly this. Copy it into the job
folder (it is uploaded with the job's other files) and call it with
`ENV_PARENT`, `ENV_NAME` and optionally `ENV_SPEC` set:
`bash "$RUN_DIR_Lux/build_conda_env.sh"` from `setup_lux.sh` on Lux, or the
same with `$RUN_DIR_Frontier` / `$RUN_DIR_Odo` from the job script. Fill in its
`TODO`s (the cluster's modules, the packages). Its steps, which any hand-written build should follow too:

1. **Set the proxy first.** The OLCF clusters reach the internet only through
   it. VISTA exports it for `setup_lux.sh`; everywhere else, export it yourself
   before `conda` or `pip`:
   ```bash
   export https_proxy=http://proxy.ccs.ornl.gov:3128
   export http_proxy=http://proxy.ccs.ornl.gov:3128
   ```
2. **Check the cluster's documentation** for how it wants environments built
   (which conda/Miniforge module, which compilers and MPI to build `mpi4py`
   against, where envs should live). For OLCF see https://docs.olcf.ornl.gov/.
   Ask the user to confirm anything the docs leave open rather than guessing.
3. **Do not build directly on Lustre.** It is slow for the many small files an
   environment has. Two fixes:
   - Keep conda's package cache and the pip build off Lustre:
     ```bash
     export CONDA_PKGS_DIRS=/tmp/$USER/conda_pkgs
     export TMPDIR=/tmp/$USER/tmp
     mkdir -p "$CONDA_PKGS_DIRS" "$TMPDIR"
     ```
     Conda hard-links from its cache into the env, which is slow across
     filesystems or on Lustre; pip compiles packages such as `mpi4py` in
     `TMPDIR`, so this moves the compile to local disk.
   - Set the env's parent directory to a stripe count of 1 before creating the
     env. Small files gain nothing from striping, and it cuts OST overhead:
     ```bash
     mkdir -p "$ENV_PARENT" && lfs setstripe -c 1 "$ENV_PARENT"
     ```

Then `conda create --prefix "$ENV_PARENT/<name>" ...` (a prefix, not a name, so
it lands in `$VISTA_JOB_DIR`), and record what was installed
(`conda env export`) in the run's log. Put the build's own time in the job's
`duration`, or tell the user the first run is slower.

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

## Wrapping existing code

The user has code that already runs (or nearly runs) on a cluster, in its own
layout, and wants it as a VISTA HPC skill. Do not rewrite their code: write a
thin job around it.

### a. Get the code into the sandbox

Never download code from the sandbox (no `curl`, `pip download` or `git clone`
of the user's repository): it has no credentials, and on some networks it does
not trust the HTTPS proxy. Instead:

- **A repository on github.com or gitlab.com: call the `fetch_repository`
  tool** with its URL: `https://github.com/<owner>/<repo>` or
  `.../tree/<ref>/<subpath>`, or `https://gitlab.com/<group>[/<subgroup>...]/<repo>`
  or `.../-/tree/<ref>/<subpath>`. Pass `ref` for a GitLab branch whose name
  contains `/`. VISTA's backend downloads it, pinned to the commit the branch
  or tag points at now, into `/mnt/data/work/<name>/` with a `SOURCE.json`
  (clone URL, commit, `private`). It can read private repositories when the
  deployment has a token for that host; if it says the repository may be
  private, ask the user to upload an archive instead. Then run
  `python3 /mnt/skills/hpc-skill-author/scripts/inventory.py /mnt/data/work/<name>`.
- **An uploaded archive or folder:** ask the user to upload a zip of a local
  folder (or of a repository on another host), then
  ```bash
  python3 /mnt/skills/hpc-skill-author/scripts/get_source.py /mnt/data/uploads/<file>.zip --dest /mnt/data/work/<name>
  ```
  `.tar`, `.tar.gz`, `.tgz` and a folder under `/mnt/data/uploads/` work too.
  It unpacks the code and prints the inventory.

The inventory lists READMEs, build files, existing batch scripts and their
`#SBATCH` lines, entry points with their flags, and the environment variables
the code reads. Read the README and
the batch scripts it lists before asking the user anything.

If the source is already a skill (the inventory says so) with `hpc_jobs/` in
this format, it needs no wrapping: tell the user to import it with Skills →
Import → GitHub URL or Local folder. If it is a skill without `hpc_jobs/`, copy
it to `/mnt/data/output/<its name>/` and add the job to it.

### b. Decide what the job runs

From the inventory, propose: the one command the job runs (an existing batch
script's body or an entry point), which of its flags become `script_args`
keys, what it needs installed, how many nodes and GPUs and how long (from
existing `#SBATCH` lines where there are any), and which files it writes.
Confirm with the user; keep everything else at the code's defaults.

Translate an existing batch script rather than calling it: VISTA writes the
`#SBATCH` header itself, so move its resources into `cluster_defaults.json`
(`-N` → `node_count`, `-t` → `duration` in seconds, `--gpus-per-node` →
`gpus_per_node`, `-p` → `iri.partition`, `-q` → `iri.queue_name`, `-C` →
`iri.constraint`), and drop its `-A`, `-o`, `-e` and `-J` lines. A `--reservation`
line is not a job default: a reservation is per submission, so it becomes the
`reservation` argument of `submit_hpc_job`, and `cluster_defaults.json` never
carries one. A `-C` is likewise only the default; the `constraint` argument
replaces it for one submission. Replace its hard-coded output paths with
`$VISTA_OUT`.

### c. Ship it or clone it

- **A public GitHub or GitLab repository: clone it on the cluster, pinned.** Use the
  `wrap-repo` template. The job folder holds only the wrapper; the repository
  is checked out at the recorded commit into `$VISTA_JOB_DIR/src/<commit>` by
  `setup_lux.sh`, once, on the Lux login node.

  ```bash
  SKILL=<skill> JOB=<job> T=/mnt/skills/hpc-skill-author/templates
  URL=$(python3 -c "import json;print(json.load(open('/mnt/data/work/<name>/SOURCE.json'))['clone_url'])")
  COMMIT=$(python3 -c "import json;print(json.load(open('/mnt/data/work/<name>/SOURCE.json'))['commit'])")
  mkdir -p /mnt/data/output/$SKILL/hpc_jobs
  cp $T/skill/SKILL.md /mnt/data/output/$SKILL/SKILL.md
  cp -r $T/wrap-repo /mnt/data/output/$SKILL/hpc_jobs/$JOB
  grep -rl '__SKILL__\|__JOB__\|__REPO_URL__\|__REPO_COMMIT__' /mnt/data/output/$SKILL \
    | xargs sed -i "s#__SKILL__#$SKILL#g; s#__JOB__#$JOB#g; s#__REPO_URL__#$URL#g; s#__REPO_COMMIT__#$COMMIT#g"
  ```

  Put one-time build steps (a `pip install`, a `make`) in `setup_lux.sh`, in
  the marked place, so they run once per commit. To move to a newer commit
  later, change `REPO_COMMIT` in `cluster_defaults.json`. On Odo and Frontier,
  where setup runs on the compute node as part of the job, clone in the job
  script instead, into `$VISTA_JOB_DIR` on Frontier or `$VISTA_OUT` on Odo, the
  same way (`git clone`, then `git checkout --detach "$REPO_COMMIT"`).
- **A private repository** (`"private": true` in SOURCE.json): ship the files
  as for an upload, below, unless the user confirms the cluster can clone it
  with their own credentials there; the skill cannot carry any.
- **An uploaded folder or archive: ship the files.** Use the `job` template and
  copy only what the job needs (sources, configs, small inputs) into
  `hpc_jobs/<job>/`, keeping their relative paths; leave out data, build
  outputs and anything large. The job reads them under `$RUN_DIR_<Cluster>`.
  The import allows at most 2000 files and 50 MB per skill.

Never copy credentials, `.env` files or SSH keys from the source into the skill.

### d. Write the SKILL.md

Say where the code comes from (the repository and commit, or "uploaded by
<user> on <date>"), what the job runs, its `script_args`, and how to read the
outputs. Then validate (step 3) and hand off (step 4) as usual.
