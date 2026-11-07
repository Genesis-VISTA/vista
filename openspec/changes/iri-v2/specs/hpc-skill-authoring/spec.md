# Spec Delta

## ADDED Requirements

### Requirement: Setup scripts run in the allocation
`hpc-skill-author` and its templates SHALL describe `setup_<cluster>.sh` the same way for
Odo, Frontier and Lux: it runs on the job's first compute node, inside the allocation,
before `job.<cluster>.slurm`, as the project's automation user, with the job's environment
and the OLCF proxy set. They SHALL NOT describe any setup step as running on a login node.
A setup script that clones or builds into `VISTA_JOB_DIR` SHALL take a lock there, so that
two jobs starting together build it once and both use the result. The Lux job template's
`cluster_defaults.json` SHALL name a partition and GPUs per node, which VISTA passes to
IRI as the queue and GPUs per process.

#### Scenario: Lux wrap-repo checkout runs in the job
- **WHEN** a job made from the `wrap-repo` template is submitted to Lux
- **THEN** its `setup_lux.sh` checks out the recorded commit from the compute node through the proxy, and nothing is run on a Lux login node

#### Scenario: Two jobs start together
- **WHEN** two submissions of the same wrapped Lux job start at the same time with no checkout yet
- **THEN** one clones the recorded commit, the other waits for it, and both run against the same checkout

#### Scenario: The guidance names no login node
- **WHEN** the `hpc-skill-author` SKILL.md and its Lux templates are read
- **THEN** none of them says a setup script runs on a login node or before `sbatch`

### Requirement: The validator refuses what submit refuses
The `hpc-skill-author` validator SHALL report as errors the `cluster_defaults.json` that
submission refuses: `iri.pre_launch`, and a `gpus_per_node` that is not a multiple of
`processes_per_node`. The skill SHALL say that on Lux each task of a step sees only its
own GPUs, and how a job that picks its GPU by local rank sees all of them.

#### Scenario: pre_launch in a job
- **WHEN** the validator checks a job whose `cluster_defaults.json` sets `iri.pre_launch`
- **THEN** it reports an error naming `setup_<cluster>.sh` as the replacement

#### Scenario: GPUs that do not divide among processes
- **WHEN** a job asks for 8 GPUs and 3 processes per node
- **THEN** the validator reports an error before any submission
