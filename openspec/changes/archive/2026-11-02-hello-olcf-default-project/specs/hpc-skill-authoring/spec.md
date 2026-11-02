## MODIFIED Requirements

### Requirement: Bundled HPC skill authoring skill

The system SHALL bundle a skill named `hpc-skill-author` that tells the agent how
to write a skill shipping HPC jobs: the folder layout (`SKILL.md`,
`hpc_jobs/<job>/` with `README.md`, `cluster_defaults.json`, `job.<cluster>.slurm`
and optional `setup_<cluster>.sh`), the job environment (`VISTA_OUT`,
`RUN_DIR_<Cluster>`, `VISTA_JOB_DIR`), `script_args` as a `KEY=VALUE` allow-list,
and that a job writes only under `VISTA_OUT` and `VISTA_JOB_DIR`. It SHALL
include templates for a skill and a job, with the job template covering Odo,
Frontier and Lux, and SHALL tell the agent to write the new skill to
`/mnt/data/output/<skill>/`, where `<skill>` is the new SKILL.md's `name`.

It SHALL recommend that the job use an environment the user has already built,
and, for a job that must build a conda environment, SHALL give these steps and
a template script that follows them: export the OLCF proxy
(`http://proxy.ccs.ornl.gov:3128`) before the build; check the cluster's
documentation (for OLCF, the `olcf-docs` knowledge base when the project has it,
otherwise https://docs.olcf.ornl.gov/); keep conda's package cache and pip's
build directory off Lustre (`CONDA_PKGS_DIRS` and `TMPDIR` under `/tmp/$USER`);
and set the environment's parent directory to a Lustre stripe count of 1 before
creating it. The template SHALL build the environment once per name under a lock
and reuse it on later runs.

#### Scenario: A job that builds its own conda environment
- **WHEN** the template build script runs twice for the same environment
- **THEN** the first run builds it with the proxy set, the package cache and temporary files under `/tmp/$USER`, and the parent striped to 1, and the second run reuses it without building

#### Scenario: A researcher asks for an HPC skill
- **WHEN** a project loads `hpc-skill-author` and the researcher asks the agent to make an HPC skill for a workflow
- **THEN** the agent writes the skill to `/mnt/data/output/<skill>/`, runs the validator on it, and tells the researcher how to import it

#### Scenario: A skill for Frontier
- **WHEN** a researcher in `hello-olcf` asks the agent for a skill that runs their script on Frontier
- **THEN** the agent starts from the Frontier job template, and the skill it writes passes the validator with a `job.frontier.slurm` and a `frontier` entry in its `cluster_defaults.json`

#### Scenario: Every template passes the validator
- **WHEN** the validator is run on the job template filled in for Odo, Frontier and Lux
- **THEN** it passes for each
