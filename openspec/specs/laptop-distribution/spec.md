# laptop-distribution Specification

## Purpose

Lets VISTA be handed to a researcher as one self-contained, relocatable artifact that runs
on their own machine with no host prerequisites to install, and that they can pass on to a
colleague on the same platform.

## Requirements

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

### Requirement: Relocatable and transferable

The artifact SHALL run from any filesystem location it is unpacked into, and SHALL run on
a different machine of the same platform than the one that built it.

#### Scenario: Unpacked to an arbitrary path

- **WHEN** the artifact is unpacked to a location whose path differs in depth and name from
  the one used to build it
- **THEN** every service starts and functions normally

#### Scenario: Copied to a colleague

- **WHEN** the artifact is copied to another machine of the same operating system and
  processor architecture and started there
- **THEN** it performs first-run setup and starts normally, with none of the sending
  machine's data present

### Requirement: Mutable state lives outside the artifact

All state the running application creates or modifies SHALL live outside the unpacked
artifact. The artifact's own contents SHALL be treated as read-only at run time.

#### Scenario: Copying carries no personal data

- **WHEN** an artifact that has been run is copied to another machine
- **THEN** the recipient's instance contains none of the sender's conversations, uploaded
  files, or configured credentials

#### Scenario: Replacing the artifact preserves state

- **WHEN** the unpacked artifact is replaced with a newer version and started
- **THEN** existing conversations, uploaded files, and configured credentials remain
  available

### Requirement: Single command to install and run

A single command SHALL perform any outstanding first-run setup and then start VISTA. That
command SHALL be safe to re-run, and on a subsequent run SHALL skip work already done.

#### Scenario: First run

- **WHEN** the command is run for the first time after unpacking
- **THEN** it prepares the sandbox image, creates the state directory, installs the bundled
  corpus, seeds the database, starts the services, and reports the address to open

#### Scenario: Subsequent run

- **WHEN** the command is run again on an already-initialised installation
- **THEN** it starts the services without repeating setup and without re-importing the
  sandbox image

### Requirement: Preconditions reported before failure

Conditions that would prevent a successful start SHALL be detected and reported by name
before services are launched.

#### Scenario: A required port is occupied

- **WHEN** another process already holds one of the ports VISTA needs
- **THEN** startup stops with a message naming the port and the conflict, rather than
  waiting indefinitely for a service to answer

#### Scenario: Wrong platform artifact

- **WHEN** an artifact built for one operating system or architecture is run on another
- **THEN** it refuses to start and names the platform the artifact was built for

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

### Requirement: Bundled corpus is searchable at first run

The artifact SHALL include the molten-salt corpus already indexed, so retrieval works on
first run without an indexing step. A knowledge base SHALL NOT be presented as ready
unless its index actually contains content.

#### Scenario: Retrieval works immediately

- **WHEN** a researcher asks a question on their first session after installation
- **THEN** retrieval returns passages from the bundled corpus with no indexing having run

#### Scenario: Cited sources are readable

- **WHEN** retrieval cites a document from the bundled corpus
- **THEN** that document can be opened from the interface

#### Scenario: Missing or empty index is refused

- **WHEN** the bundled index is absent or contains no entries at first run
- **THEN** setup fails with a message identifying the incomplete corpus, rather than
  creating a knowledge base that reports itself ready and returns nothing

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

### Requirement: Building for another platform

A build host SHALL be able to produce an artifact for a supported platform other than its
own, without access to a machine of that platform. Such an artifact SHALL be equivalent to
one built natively for the target.

#### Scenario: Building for a different platform

- **WHEN** a build is run for a supported target platform that differs from the build
  host's
- **THEN** it produces an artifact whose bundled interpreter, language runtime, compiled
  dependencies, and sandbox image are all the target platform's

#### Scenario: A host component is not substituted for a target one

- **WHEN** a component staged into the artifact could be taken either from the build host
  or from the target environment
- **THEN** the copy placed in the artifact is the target platform's, and a component that
  cannot be obtained for the target fails the build rather than being substituted

#### Scenario: The cross-built artifact is still exercised

- **WHEN** a cross-platform build completes
- **THEN** the artifact is started and checked in a target-platform environment before the
  build reports success, on the same terms as a native build

### Requirement: Stated compatibility floor

An artifact SHALL record the oldest target-platform system libraries it supports, so a host
that cannot run it can be identified without unpacking and starting it.

#### Scenario: Reading an artifact's requirements

- **WHEN** the recorded contents of an artifact are inspected
- **THEN** they state the platform it targets and the minimum system library version that
  platform needs

### Requirement: Build-time verification

The build SHALL verify its own prerequisites before producing an artifact, and SHALL verify
the finished artifact before it is considered complete. Verification SHALL include starting
the artifact from a location other than the one it was built in.

#### Scenario: A build prerequisite is missing

- **WHEN** a build is started without one of its required credentials or tools
- **THEN** it fails immediately, naming what is missing, rather than producing an
  incomplete artifact

#### Scenario: The finished artifact is exercised

- **WHEN** a build completes
- **THEN** the artifact is unpacked to a different location, started, checked for service
  health, checked with one retrieval query, and shut down before the build reports success

#### Scenario: Contents are recorded

- **WHEN** a build completes
- **THEN** it records the components included and their sizes, so an incomplete artifact
  can be identified without unpacking it
