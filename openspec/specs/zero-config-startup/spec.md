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

Setup for optional file-transfer infrastructure SHALL be performed when the researcher
asks for it, and SHALL NOT be a condition of starting. No service SHALL wait on it, and no
failure of it SHALL prevent any other service from starting or delay a start. Where setup
requires the researcher to authenticate with an external service, the instructions SHALL be
presented rather than assumed, and SHALL remain completable on a host that cannot open a
browser.

Its state SHALL be reportable at any time, and a failure SHALL be reported with a statement
of what becomes unavailable.

#### Scenario: Transfer credentials absent

- **WHEN** VISTA is launched on an installation where file transfer has never been
  connected
- **THEN** no transfer setup is attempted, nothing is asked of the researcher, and every
  service starts normally

#### Scenario: Transfer credentials present but setup cannot complete

- **WHEN** the researcher asks to connect file transfer and setup fails, for any reason
  including no network, a declined authorization, or an unavailable transfer endpoint
- **THEN** every other service is unaffected, and the failure is reported with the clusters
  whose file operations remain unavailable

#### Scenario: First-run login is presented, not assumed

- **WHEN** file-transfer setup requires the researcher to authenticate with an external
  service before the transfer endpoint can be created
- **THEN** the address to authenticate at is displayed, and setup waits for the researcher
  to supply what the external service returns

#### Scenario: Setup already completed

- **WHEN** VISTA is launched on an installation whose transfer endpoint was set up on a
  previous run
- **THEN** no authorization is requested and file operations work without researcher
  interaction

#### Scenario: Host that cannot open a browser

- **WHEN** setup would direct the researcher to a web address on a host where no browser
  can be launched
- **THEN** the address is still displayed in a form the researcher can copy elsewhere, and
  setup completes

#### Scenario: Dependent tools report the cause

- **WHEN** a job tool that depends on file transfer is invoked before setup has completed
- **THEN** it fails with a message naming the incomplete setup rather than an internal error

#### Scenario: The tool's remedy is one the researcher can perform

- **WHEN** a job tool reports that file-transfer setup is incomplete
- **THEN** the remedy it names is available in the researcher's installation, rather than a
  step that only a source checkout provides

### Requirement: File transfer is isolated from agent code execution

The transfer endpoint holds credentials that authorize movement of the researcher's files.
It SHALL NOT share an execution environment with agent-generated code, and SHALL NOT be
able to read credentials it does not itself require.

#### Scenario: Agent code cannot reach transfer credentials

- **WHEN** agent-generated code runs in the code-execution sandbox while a transfer
  endpoint is running
- **THEN** that code has no access to the transfer endpoint's execution environment,
  configuration, or credentials

#### Scenario: The transfer endpoint sees only what it moves

- **WHEN** the transfer endpoint is running
- **THEN** the only host locations readable or writable from its execution environment are
  those holding the files it transfers and its own configuration, and the application's
  stored credentials are not among them

### Requirement: File-transfer credentials supplied through the interface

VISTA SHALL accept file-transfer credentials through the running application, and SHALL
guide the researcher through obtaining one rather than assuming they can produce it
elsewhere. Obtaining it requires authorizing VISTA with an external service, so the
application SHALL present the address to authorize at and SHALL accept what that
authorization returns. A credential so supplied SHALL be stored encrypted at rest and
SHALL take effect without restarting any service.

Credentials SHALL be held per cluster. Clusters may require different identities, so one
stored credential SHALL NOT be assumed to authorize another cluster's transfers.

#### Scenario: Connecting from the interface

- **WHEN** a researcher starts the file-transfer connection for a cluster in the settings
  interface
- **THEN** the address to authorize at is displayed, and the interface accepts what the
  authorization returns

#### Scenario: Credential at rest

- **WHEN** a file-transfer credential has been stored
- **THEN** it is not readable in plaintext from the application's database file

#### Scenario: Credential effective without a restart

- **WHEN** a researcher connects file transfer while VISTA is running
- **THEN** the next file operation for that cluster uses the credential, with no service
  restart

#### Scenario: Authorization not completed

- **WHEN** the researcher abandons the authorization, or supplies something the external
  service does not accept
- **THEN** the interface reports that the connection was not made, nothing is stored, and
  the researcher can start again

#### Scenario: A second cluster is not assumed

- **WHEN** a researcher has connected file transfer for one cluster only
- **THEN** that cluster's file operations work and the other cluster reports that it is not
  connected

#### Scenario: Obtaining the credential requires nothing outside the application

- **WHEN** a researcher completes the file-transfer connection on an installation with no
  source checkout and no separately installed tooling
- **THEN** the credential is obtained and stored, without the researcher running anything
  outside VISTA

### Requirement: A deployment credential is a fallback, not a requirement

Where a deployment configures file-transfer credentials for all of its users, a credential
a researcher has supplied SHALL take precedence for that researcher, and the deployment's
SHALL be used when they have supplied none. Neither SHALL be required for VISTA to start.

#### Scenario: Researcher has connected

- **WHEN** a researcher who has connected file transfer performs a file operation on a
  deployment that also configures a credential
- **THEN** the researcher's own credential authorizes it

#### Scenario: Researcher has not connected

- **WHEN** a researcher who has not connected performs a file operation on a deployment
  that configures a credential
- **THEN** the deployment's credential authorizes it, as it did before any interface
  existed

#### Scenario: Neither is configured

- **WHEN** no credential is configured by the deployment and none has been supplied
- **THEN** VISTA starts normally and the file operation reports that file transfer is not
  connected, naming where to connect it
