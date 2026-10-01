# code-execution-sandbox Specification

## Purpose
Defines what an agent can rely on when it runs code in the sandbox. This covers how output, input, exit status and network access behave, and how the sandbox's stored state behaves across upgrades on every supported host.

## Requirements

### Requirement: Output is streamed as it is produced

A command run in the sandbox SHALL deliver its output to the caller as the command produces it, not only when it exits. When the caller asks for combined output, standard error SHALL be merged into standard output. When the caller asks for separate streams, the bytes of each stream SHALL arrive unaltered.

#### Scenario: Incremental output

- **WHEN** an agent runs a command that prints one line per second for three seconds
- **THEN** the lines reach the agent about one second apart, not all together at exit

#### Scenario: Combined streams

- **WHEN** an agent runs a command that writes to both standard output and standard error, with combined output requested
- **THEN** the agent receives both in one stream

#### Scenario: Separate streams are byte-exact

- **WHEN** a caller runs a command with separate streams and the command writes arbitrary bytes, including line feeds
- **THEN** each stream's bytes arrive unchanged, with no line-ending translation

### Requirement: Input reaches the command

Data a caller supplies as a command's standard input SHALL be delivered to that command, and closing the input SHALL signal end of file to it. A command that reads its standard input SHALL NOT wait forever when the caller supplied none.

#### Scenario: Creating a file from supplied content

- **WHEN** an agent creates a file in the sandbox by supplying its content
- **THEN** the file exists with exactly that content, and the call returns without timing out

#### Scenario: No input supplied

- **WHEN** an agent runs a command that reads standard input, and supplies no input
- **THEN** the command sees end of file and the call completes

### Requirement: Exit status is reported

The caller SHALL receive the exit status of every command it runs. A command that cannot be started SHALL be reported as a failure, and SHALL NOT make the caller wait.

#### Scenario: Non-zero exit

- **WHEN** an agent runs a command that exits with status 7
- **THEN** the reported exit status is 7

#### Scenario: Missing program

- **WHEN** an agent runs a program that does not exist in the sandbox
- **THEN** a non-zero status is reported promptly

### Requirement: Sandbox networking follows the host

The sandbox SHALL resolve names the way its host does, including resolvers the host's VPN or split-DNS configuration supplies. The sandbox SHALL NOT substitute a fixed public resolver list for the host's resolvers. Outbound access SHALL be limited to public destinations.

#### Scenario: Name resolution on a managed network

- **WHEN** the host resolves names only through its organisation's resolvers
- **THEN** a name the host can resolve also resolves inside the sandbox

#### Scenario: Public egress

- **WHEN** an agent fetches a public HTTPS URL from inside the sandbox
- **THEN** the request succeeds

### Requirement: Sandbox volumes

Host directories mounted into the sandbox SHALL appear at their configured sandbox path, with the configured access mode, on every supported host. Writes to a writable mount SHALL be visible on the host.

#### Scenario: Writable mount round-trip

- **WHEN** a file is written on the host into a writable mount, and another is written from inside the sandbox
- **THEN** each side sees the other's file

### Requirement: Sandboxes are removed on close

Closing a sandbox SHALL stop it and remove it. A service that is killed rather than stopped cannot close its sandboxes; those stay in the store, stopped, until they are removed by hand.

#### Scenario: Close

- **WHEN** a session's sandbox is closed
- **THEN** the sandbox runtime no longer lists it

### Requirement: Sandbox store survives upgrade

Upgrading the sandbox runtime SHALL carry an installation's existing sandbox store forward in place, without requiring the researcher to re-import or rebuild the sandbox image. Where the upgrade leaves the store unreadable to the previous runtime version, that SHALL be documented, together with the reset that lets the previous version start again. For the 0.5.x upgrade this lives in the `windows-support` change's migration note.

#### Scenario: Upgrading an installation

- **WHEN** an installation, checkout or deployment whose store holds the sandbox image is upgraded to the new runtime version and started
- **THEN** its sandbox starts without the image being imported or rebuilt again

#### Scenario: Rolling back after an upgrade

- **WHEN** an upgraded installation is rolled back to the previous runtime version and its documented reset is followed
- **THEN** the previous version starts its sandbox
