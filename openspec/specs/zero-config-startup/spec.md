# zero-config-startup Specification

## Purpose

Lets VISTA start and serve with no configuration file present, so the only value a
researcher must supply is an inference credential, entered through the application rather
than assembled on disk before first launch.

## Requirements

### Requirement: Startup without configuration

Every VISTA service SHALL start successfully when no configuration file is present and no
VISTA environment variables are set. No configuration value SHALL be mandatory at process
startup.

#### Scenario: No configuration file exists

- **WHEN** VISTA is started on a host with no configuration file and no VISTA environment
  variables set
- **THEN** all services start, the web interface is reachable, and no service exits with a
  missing-configuration error

#### Scenario: Existing configuration is still honoured

- **WHEN** VISTA is started on a host where a configuration file supplies an inference
  model and endpoint
- **THEN** those values take effect and behavior is unchanged from before this capability
  existed

### Requirement: Default inference target

VISTA SHALL ship a default inference model and a default OpenAI-compatible endpoint, so
that an access credential is the only outstanding value after installation.

#### Scenario: Only a credential is outstanding

- **WHEN** a researcher installs VISTA and supplies nothing
- **THEN** the model and endpoint are already set, and the interface identifies the access
  credential as the single remaining requirement

### Requirement: Inference credentials supplied through the interface

VISTA SHALL accept its inference credentials through the running application. A credential
so supplied SHALL be stored encrypted at rest and SHALL take effect on the next request
without restarting any service.

#### Scenario: Credential entered after startup

- **WHEN** a researcher enters an inference credential in the settings interface of a
  running VISTA
- **THEN** the next message uses that credential, with no service restart

#### Scenario: Credential at rest

- **WHEN** an inference credential has been stored
- **THEN** it is not readable in plaintext from the application's database file

### Requirement: Missing inference credentials are a reported state

An absent or rejected inference credential SHALL be reported as a named condition
directing the researcher to where it is configured. It SHALL NOT crash a service, and
SHALL NOT prevent any feature that does not require inference from working.

#### Scenario: Chat attempted with no credential

- **WHEN** a researcher sends a message before configuring a credential
- **THEN** the response states that an inference credential is required and where to enter
  it, rather than surfacing an internal error

#### Scenario: Non-inference features remain available

- **WHEN** no inference credential is configured
- **THEN** projects, skills, and knowledge bases remain browsable

### Requirement: Retrieval requires no external account

The embedding model that retrieval depends on SHALL be usable without account
registration, terms-of-use acceptance, or any access credential, and SHALL load without
contacting a network service.

#### Scenario: Retrieval on a first run with no accounts

- **WHEN** VISTA starts on a host with no credentials of any kind configured
- **THEN** the retrieval service starts and answers queries against any indexed corpus

#### Scenario: No outbound request to fetch the model

- **WHEN** the retrieval service starts with outbound network access blocked
- **THEN** it still loads its embedding model and serves queries

### Requirement: No credential field discards its value

Every credential or configuration field the interface presents SHALL be read by the
component it names. A field whose value no component consumes SHALL NOT be presented.

#### Scenario: A field that nothing reads

- **WHEN** the interface offers a field for a credential that no component consumes
- **THEN** that field is removed rather than left accepting input

### Requirement: Optional file-transfer setup never blocks startup

Setup for optional file-transfer infrastructure SHALL be attempted only when the
credentials that make it usable are configured. Its failure SHALL NOT prevent any other
service from starting, and SHALL be reported with a statement of what becomes unavailable.

#### Scenario: Transfer credentials absent

- **WHEN** VISTA is launched with no file-transfer credentials configured
- **THEN** no transfer setup is attempted and every service starts normally

#### Scenario: Transfer credentials present but setup cannot complete

- **WHEN** transfer credentials are configured but setup fails, for any reason including
  an absent host prerequisite or no interactive terminal
- **THEN** the launch continues, every other service starts, and a warning names the
  clusters whose file operations are unavailable

#### Scenario: Dependent tools report the cause

- **WHEN** a job tool that depends on file transfer is invoked after setup did not complete
- **THEN** it fails with a message naming the incomplete setup rather than an internal error
