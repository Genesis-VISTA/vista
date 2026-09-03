## ADDED Requirements

### Requirement: Compute-node S3 push for storage-less IRI clusters

Clusters whose IRI service does not authorize storage (Odo, Frontier) SHALL retrieve job
output by having the job upload it to a Vista-owned S3 bucket. Vista MUST NOT perform any
file operation against those clusters' filesystems.

#### Scenario: OLCF submission injects the push contract

- **WHEN** a job is submitted to Odo or Frontier
- **THEN** the IRI JobSpec environment SHALL carry the S3 bucket, key prefix, and
  object-store credential as `VISTA_S3_*` variables
- **AND** the inlined setup snippet SHALL install an exit trap that uploads `$VISTA_OUT`
  and the Slurm stdout/stderr logs
- **AND** submission SHALL fail with a `ToolError` before reaching the cluster when the
  credential is not configured

#### Scenario: Clusters with IRI storage are unaffected

- **WHEN** a job is submitted to Perlmutter
- **THEN** file operations SHALL continue to use the IRI filesystem API
- **AND** no S3 credential SHALL be required for that path

#### Scenario: The credential cannot delete

- **WHEN** the deployment's IAM policy is configured
- **THEN** it SHALL grant no `s3:DeleteObject` on the job output prefix, since no tool
  path deletes and retention is a bucket lifecycle rule
- **AND** bucket versioning SHALL be enabled, so an overwrite by a leaked key is
  recoverable

#### Scenario: Credentials are not named AWS_*

- **WHEN** the JobSpec environment is constructed
- **THEN** the credential variables SHALL NOT use the `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` names reserved by VISTAGuard's G5
  credential denylist

### Requirement: Job output contract — VISTA_OUT and VISTA_SCRATCH

The submission prefix SHALL export `$VISTA_OUT` and `$VISTA_SCRATCH` on every cluster.
Everything under `$VISTA_OUT` SHALL be uploaded verbatim with no exclusion or size
filtering; `$VISTA_SCRATCH` SHALL never be uploaded and SHALL be removed after the upload.

#### Scenario: Scratch is excluded by location, not by pattern

- **WHEN** a job writes a virtualenv, git clone, or package cache under `$VISTA_SCRATCH`
- **THEN** those files SHALL NOT appear in the uploaded object set
- **AND** the scratch directory SHALL be deleted by the exit trap

#### Scenario: HOME defaults to scratch

- **WHEN** the compute environment supplies no `HOME`
- **THEN** the prefix SHALL default `HOME` to `$VISTA_SCRATCH`, not `$VISTA_OUT`, so
  dotfiles written by pip or matplotlib are not uploaded

#### Scenario: Output is uploaded unfiltered

- **WHEN** a job writes files of any size or extension under `$VISTA_OUT`
- **THEN** every one of them SHALL be uploaded, including binaries over the 5 GiB
  single-request ceiling via multipart upload

### Requirement: Per-file objects with a completion manifest

The uploader SHALL write one object per output file, preserving paths relative to
`$VISTA_OUT`, and SHALL write `manifest.json` last as a completion sentinel.

#### Scenario: Selective retrieval

- **WHEN** `get_hpc_job_outputs` is asked for a subset of files
- **THEN** only those objects SHALL be downloaded
- **AND** bytes SHALL land in `host_output_dir/<job_id>/<relpath>` while the returned
  paths SHALL be `/mnt/data/output/<job_id>/<relpath>`
- **AND** relative paths containing `..` or absolute paths SHALL be rejected

#### Scenario: Truncated push is distinguishable from an empty one

- **WHEN** the IRI state is terminal but no `manifest.json` object exists
- **THEN** `get_hpc_job_status` SHALL report that the output push did not complete
- **AND** it MUST NOT report the job as having produced no output

### Requirement: Job sources are inlined, not transferred

Job supporting files SHALL be materialized on the cluster by the inlined setup snippet
rather than uploaded, and `RUN_DIR_<Cluster>` SHALL live under `$VISTA_SCRATCH`.

#### Scenario: No cluster-side read credential

- **WHEN** an OLCF job is submitted
- **THEN** the non-metadata files in `hpc_jobs/<job>/` SHALL be embedded in the JobSpec
- **AND** no credential granting read access to Vista's object store SHALL be sent to the
  cluster

## MODIFIED Requirements

### Requirement: Fake IRI and S3 clients

Unit tests SHALL exercise JobSpec construction for Odo, Perlmutter, and Frontier
using `FakeIriClient` / `FakeS3Client` without network access.

#### Scenario: JobSpec happy paths with network disabled

- **WHEN** submit-path unit tests run with network disabled
- **THEN** Odo, Perlmutter, and Frontier JobSpec assertions SHALL pass
- **AND** they MUST NOT require real IRI or AWS credentials

#### Scenario: The compute-node uploader is covered locally

- **WHEN** uploader unit tests run
- **THEN** SigV4 signing, the `$VISTA_OUT` walk, multipart splitting, and
  manifest-written-last ordering SHALL be asserted without network access

### Requirement: Hermetic PR CI

PR and default local CI SHALL run without AmSC API keys, AWS credentials,
or real Slurm / cluster access.

#### Scenario: Default merge pipeline needs no live secrets

- **WHEN** a developer runs `./scripts/ci-local.sh test` or a GitLab MR pipeline
- **THEN** all required jobs complete without live LLM, AWS, or HPC credentials
- **AND** failures that require those secrets MUST NOT block merges

### Requirement: Fake at boundaries

Tests SHALL mock external IRI, S3, and LLM clients at system boundaries
while keeping catalog parsing, metadata injection, and HPC dry-run paths real.

#### Scenario: Network-disabled submit-path unit tests

- **WHEN** HPC submit-path unit tests run with network disabled
- **THEN** they SHALL pass using fakes / dry-run
- **AND** they MUST NOT call real IRI or S3 endpoints
