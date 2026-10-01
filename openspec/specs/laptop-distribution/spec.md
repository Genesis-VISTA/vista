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
command SHALL be safe to re-run, and on a subsequent run SHALL skip work already done. The
command SHALL end by opening VISTA in its application window; VISTA has no browser mode, and
a session that cannot show the window is refused before any service starts.

#### Scenario: First run

- **WHEN** the command is run for the first time after unpacking
- **THEN** it prepares the sandbox image, creates the state directory, installs the bundled
  corpus, seeds the database, starts the services, and opens the VISTA window

#### Scenario: Subsequent run

- **WHEN** the command is run again on an already-initialised installation
- **THEN** it starts the services without repeating setup and without re-importing the
  sandbox image

#### Scenario: No window available

- **WHEN** the command is run in a session that cannot show the window, such as one with no
  display or a remote shell session
- **THEN** it says why the window cannot open and exits without starting any service

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

The artifact SHALL include the AI-safety corpus already indexed, so retrieval works on
first run without an indexing step. A knowledge base SHALL NOT be presented as ready
unless its index actually contains content. The molten-salt corpus, its index and MSTDB
data SHALL be included only in an artifact built with the science projects enabled; an
artifact built without them SHALL contain none of that data.

#### Scenario: Retrieval works immediately

- **WHEN** a researcher asks a question on their first session after installation
- **THEN** retrieval returns passages from the bundled AI-safety corpus with no indexing
  having run

#### Scenario: Cited sources are readable

- **WHEN** retrieval cites a document from a bundled corpus
- **THEN** that document can be opened from the interface

#### Scenario: Missing or empty index is refused

- **WHEN** a bundled index is absent or contains no entries at first run
- **THEN** setup fails with a message identifying the incomplete corpus, rather than
  creating a knowledge base that reports itself ready and returns nothing

#### Scenario: The installed artifact never indexes

- **WHEN** an installed artifact starts, on first run or later, including when a bundled
  index is missing
- **THEN** no corpus is embedded and no citation-extraction call is made on the
  researcher's machine; a missing index fails setup as above

#### Scenario: Default artifact carries no science data

- **WHEN** an artifact is built without the science projects enabled
- **THEN** it contains no molten-salt corpus, no molten-salt index and no MSTDB file,
  including inside the HPC job catalog

#### Scenario: Science artifact

- **WHEN** an artifact is built with the science projects enabled
- **THEN** it also contains the molten-salt corpus already indexed and the MSTDB data, and
  retrieval against the molten-salt corpus works on first run

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

### Requirement: Stated compatibility floor

An artifact SHALL record the oldest target-platform system libraries it supports, so a host
that cannot run it can be identified without unpacking and starting it. On macOS and Windows,
where the build host's own version is the floor, the artifact SHALL record the version of the
host it was built on.

#### Scenario: Reading an artifact's requirements

- **WHEN** the recorded contents of an artifact are inspected
- **THEN** they state the platform it targets and the minimum system library version that
  platform needs

### Requirement: Build-time verification

The build SHALL verify its own prerequisites before producing an artifact, and SHALL verify
the finished artifact before it is considered complete. Verification SHALL include starting
the artifact from a location other than the one it was built in.

#### Scenario: A build prerequisite is missing

- **WHEN** a build is started without one of its required credentials or tools, or without
  the AI-safety corpus in its source data
- **THEN** it fails immediately, naming what is missing, rather than producing an
  incomplete artifact

#### Scenario: Science data required only when enabled

- **WHEN** a build is started without the science projects enabled and its source data
  lacks the molten-salt corpus or MSTDB
- **THEN** the build proceeds

#### Scenario: The finished artifact is exercised

- **WHEN** a build completes
- **THEN** the artifact is unpacked to a different location, started, checked for service
  health, checked with one retrieval query against the AI-safety corpus, checked to contain
  no science data unless it was built with the science projects enabled, and shut down
  before the build reports success

#### Scenario: Contents are recorded

- **WHEN** a build completes
- **THEN** it records the components included and their sizes, so an incomplete artifact
  can be identified without unpacking it

### Requirement: Upgraded installations receive new bundled corpora

When a newer artifact is run against an existing state directory, first-run setup SHALL
install any bundled corpus the state directory does not already have, even when other
bundled corpora are already installed. It SHALL NOT replace or remove a corpus already
installed.

#### Scenario: Upgrading from a molten-salt package

- **WHEN** a package containing the AI-safety corpus is run against a state directory
  created by an earlier package that installed only the molten-salt corpus
- **THEN** the AI-safety corpus and its index are installed, the molten-salt corpus is left
  in place, and the AI-safety project's literature search works in that session

#### Scenario: Nothing new to install

- **WHEN** a package is run against a state directory that already holds every corpus it
  bundles
- **THEN** no corpus is extracted or overwritten

### Requirement: Windows x64 is a supported platform

VISTA SHALL be distributable as an artifact for Windows 10 and 11 on x64. The artifact SHALL meet every other requirement of this capability. It SHALL be started by `vista.cmd`, which needs no shell other than cmd or PowerShell.

#### Scenario: First run on Windows

- **WHEN** a Windows x64 artifact is unpacked and `vista.cmd` is run from cmd, PowerShell or Explorer on a host that meets its preconditions
- **THEN** it performs first-run setup, starts every service including the code-execution sandbox, and opens the VISTA window

#### Scenario: Agent round trip on Windows

- **WHEN** a researcher on a Windows host sends an agent a message that runs code, writes a plot and displays it
- **THEN** the plot is displayed in the interface

### Requirement: Windows preconditions reported before failure

On Windows, the launcher SHALL check the host features the sandbox and the unpacked files need, and SHALL name any that are missing before starting a service. For each one it SHALL say how to meet it, and whether meeting it needs administrator rights.

#### Scenario: Sandbox hypervisor not available

- **WHEN** the artifact is started on a Windows host where the hypervisor the code-execution sandbox uses is not available
- **THEN** it says so, names Windows Hypervisor Platform as what provides it, says enabling it needs administrator rights and a restart, and starts no service

#### Scenario: Unpacked path too long

- **WHEN** the artifact is unpacked where one of its files' full paths exceeds the Windows path-length limit, and long paths are not enabled on the host
- **THEN** it reports the limit and suggests a shorter unpack location or enabling long paths, before starting any service

#### Scenario: State directory too deep

- **WHEN** the state directory is deep enough that a corpus file extracted into it would exceed the Windows path-length limit, and long paths are not enabled on the host
- **THEN** it reports the limit and suggests a shorter `VISTA_HOME`, before starting any service

#### Scenario: Extracted to Downloads with Explorer

- **WHEN** a Windows artifact is extracted with Explorer's Extract All, at its default destination, in the user's Downloads folder on a host without long paths enabled
- **THEN** every file is extracted and the artifact starts

### Requirement: Built and verified on its own platform

An artifact SHALL be built on a host of the platform it targets. That host SHALL be able to run the code-execution sandbox, because the build's verification of the finished artifact SHALL include the checks that go through the sandbox, retrieval among them. No check SHALL be skipped for want of hardware virtualisation; verification is skipped only on an explicit maintainer option.

#### Scenario: Building for Windows

- **WHEN** a Windows artifact is built on a Windows build host
- **THEN** its bundled interpreter, runtime and dependencies are the Windows x64 ones, its sandbox image is the Linux x64 image, and the build verifies it as any other build does

#### Scenario: A build host that cannot run the sandbox

- **WHEN** a build whose verification is not explicitly skipped runs on a host where the code-execution sandbox cannot run
- **THEN** the build's verification fails, the build does not report success, and the launcher's message naming the missing requirement is shown
