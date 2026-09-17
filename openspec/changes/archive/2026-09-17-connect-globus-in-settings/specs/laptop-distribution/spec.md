## MODIFIED Requirements

### Requirement: Job submission preserved

The artifact SHALL include the private dependency that HPC job submission requires, so
that a researcher can submit jobs to a supported cluster without obtaining access to any
private software repository. Per-user cluster credentials SHALL be entered through the
interface. For a cluster whose file operations require a transfer endpoint local to the
researcher's machine, the artifact SHALL carry what that endpoint needs, so the researcher
installs nothing to submit jobs there.

Where a credential cannot simply be typed in because it must first be issued by an external
service, the artifact SHALL carry the means to obtain it. A credential the researcher can
only acquire from a source checkout SHALL NOT be a condition of any capability the artifact
offers.

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

#### Scenario: A credential that must be issued rather than typed

- **WHEN** a capability depends on a credential that an external service issues after the
  researcher authorizes VISTA
- **THEN** the artifact guides the researcher through obtaining it, and no step requires a
  source checkout, a development tool, or a file the artifact does not contain
