## MODIFIED Requirements

### Requirement: Self-contained artifact

The distributed artifact SHALL contain everything needed to run VISTA. Running it SHALL
NOT require a container runtime, a language runtime, a package manager, or a model
download on the researcher's machine. No capability the artifact offers SHALL depend on a
container runtime being present, including file transfer to remote clusters.

#### Scenario: Host with no container runtime

- **WHEN** the artifact is unpacked and started on a supported host with no container
  runtime installed
- **THEN** every service starts, including the code-execution sandbox

#### Scenario: File transfer on a host with no container runtime

- **WHEN** file-transfer credentials are configured on a supported host with no container
  runtime installed, and a job tool performs a file operation against a cluster that
  requires a local transfer endpoint
- **THEN** the operation succeeds, without the researcher installing anything

#### Scenario: Host with no network access

- **WHEN** the artifact is started on a supported host with outbound network access blocked
- **THEN** startup completes and the bundled corpus is searchable; only features that call
  an external service are unavailable

### Requirement: Job submission preserved

The artifact SHALL include the private dependency that HPC job submission requires, so
that a researcher can submit jobs to a supported cluster without obtaining access to any
private software repository. Per-user cluster credentials SHALL be entered through the
interface. For a cluster whose file operations require a transfer endpoint local to the
researcher's machine, the artifact SHALL carry what that endpoint needs, so the researcher
installs nothing to submit jobs there.

#### Scenario: Submitting to a supported cluster

- **WHEN** a researcher enters their cluster account, remote directory, and access token in
  the settings interface
- **THEN** job submission, status polling, and output retrieval work for that cluster with
  no further installation

#### Scenario: Submitting to a cluster that needs a local transfer endpoint

- **WHEN** a researcher submits to a supported cluster whose uploads and downloads are
  brokered between two transfer endpoints, one of which must be their own machine
- **THEN** submission, status polling, and output retrieval work with no transfer software
  installed by the researcher

### Requirement: Declared platform support

The supported platforms SHALL be stated, and a host that cannot meet the requirements
SHALL be told which requirement it fails rather than failing partway through startup. A
requirement that affects only one capability SHALL be reported as affecting that
capability, not as a reason to refuse to start.

#### Scenario: Unsupported processor or system libraries

- **WHEN** the artifact is started on a host whose processor or system libraries cannot
  support the sandbox
- **THEN** it reports the specific unmet requirement before starting any service

#### Scenario: Hardware virtualisation unavailable

- **WHEN** the artifact is started on a host where the code-execution sandbox cannot obtain
  hardware virtualisation
- **THEN** it reports that requirement by name before starting any service, rather than
  failing on the researcher's first agent message

#### Scenario: A prerequisite that only file transfer needs

- **WHEN** the artifact is started on a host that satisfies every requirement except one
  that only the file-transfer endpoint depends on
- **THEN** every other service starts and the shortfall is reported as affecting file
  transfer for the named clusters
