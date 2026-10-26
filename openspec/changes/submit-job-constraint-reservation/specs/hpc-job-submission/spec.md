## ADDED Requirements

### Requirement: Per-submission constraint and reservation
Job submission SHALL accept an optional Slurm constraint and an optional Slurm
reservation on every cluster, given per submission. A constraint given at
submission SHALL replace the job's default constraint, not combine with it. A
reservation SHALL come only from the submission and never from a job's
defaults.

Before anything is submitted, and in dry-run mode too, the system SHALL refuse
values that are not Slurm syntax. A constraint is letters, digits and
`_ - . : , * & | [ ]`. A reservation is a name of letters, digits and
`_ - . :`. Refused values include whitespace and newlines.

The system SHALL pass the effective constraint and the reservation to Slurm as
follows:
- Lux: `#SBATCH -C` and `#SBATCH --reservation=`.
- Perlmutter, Odo and Frontier: the IRI job's `constraint` custom attribute and
  its reservation id.
- No constraint attribute SHALL be sent when there is no constraint.

The submission result SHALL report the effective constraint and reservation.

On Odo and Frontier, after submitting a job with a constraint or a
reservation, the system SHALL read Slurm's record of that job:
- If the record shows no constraint where one was requested, or no reservation
  where one was requested, the system SHALL cancel the job and fail. The
  failure SHALL say that OLCF did not pass the constraint or reservation to
  Slurm, and SHALL name Perlmutter and Lux as the clusters that take a
  constraint.
- Any non-empty recorded constraint SHALL count as passed.
- If the record cannot be read, or lacks those fields, the system SHALL keep
  the job, and the submission result SHALL say the constraint or reservation
  is unverified.

#### Scenario: Constraint replaces the default on Perlmutter
- **WHEN** a job whose Perlmutter default constraint is `gpu` is submitted to Perlmutter with constraint `gpu&hbm80g`
- **THEN** the IRI job carries constraint `gpu&hbm80g` and no other constraint

#### Scenario: CPU constraint on a GPU job
- **WHEN** a job whose Perlmutter default constraint is `gpu` is submitted to Perlmutter with constraint `cpu`
- **THEN** the job is submitted with constraint `cpu` and without a GPUs-per-node setting

#### Scenario: Lux batch script
- **WHEN** a job is submitted to Lux with constraint `nvme` and reservation `train1`
- **THEN** its batch script contains `#SBATCH -C nvme` and `#SBATCH --reservation=train1`

#### Scenario: Reservation on an IRI cluster
- **WHEN** a job is submitted to Perlmutter, Odo or Frontier with reservation `train1`
- **THEN** the IRI job carries reservation id `train1`

#### Scenario: No constraint anywhere
- **WHEN** a job that sets no default constraint is submitted to Perlmutter without one
- **THEN** the IRI job carries no constraint attribute

#### Scenario: Injected value refused
- **WHEN** a submission's constraint is `nvme\n#SBATCH -A other`, or its reservation contains a space
- **THEN** submission fails before any file is moved or any cluster is contacted, naming the argument and the characters allowed

#### Scenario: Dry run refuses bad values
- **WHEN** HPC dry-run is on and a submission's constraint contains a newline
- **THEN** the submission is refused and no dry-run job is recorded

#### Scenario: OLCF drops the constraint
- **WHEN** a job is submitted to Frontier with constraint `nvme` and Slurm's record of the job shows an empty constraint
- **THEN** the job is cancelled and submission fails, saying OLCF did not pass the constraint to Slurm and naming Perlmutter and Lux

#### Scenario: Default constraint on an OLCF cluster
- **WHEN** a job whose Odo defaults set a constraint is submitted to Odo without one, and Slurm's record shows an empty constraint
- **THEN** the job is cancelled and submission fails as if the constraint had been given at submission

#### Scenario: OLCF forwards the constraint
- **WHEN** a job is submitted to Odo with constraint `nvme` and Slurm's record shows a non-empty constraint
- **THEN** the job is kept and the submission result reports constraint `nvme`

#### Scenario: Record cannot be read
- **WHEN** a job is submitted to Odo with a reservation and reading its record fails
- **THEN** the job is kept and the submission result says the reservation is unverified

#### Scenario: Nothing to check
- **WHEN** a job with no constraint and no reservation is submitted to Odo
- **THEN** Slurm's record is not read and the submission result is as before
