## MODIFIED Requirements

### Requirement: Optional file-transfer setup never blocks startup

Setup for optional file-transfer infrastructure SHALL be attempted only when the
credentials that make it usable are configured. Its failure SHALL NOT prevent any other
service from starting, and SHALL be reported with a statement of what becomes unavailable.
Where that setup requires the researcher to authenticate with an external service, the
instructions SHALL be presented rather than assumed, and SHALL remain completable on a host
that cannot open a browser.

#### Scenario: Transfer credentials absent

- **WHEN** VISTA is launched with no file-transfer credentials configured
- **THEN** no transfer setup is attempted and every service starts normally

#### Scenario: Transfer credentials present but setup cannot complete

- **WHEN** transfer credentials are configured but setup fails, for any reason including
  no interactive terminal, no network, or a declined login
- **THEN** the launch continues, every other service starts, and a warning names the
  clusters whose file operations are unavailable

#### Scenario: First-run login is presented, not assumed

- **WHEN** file-transfer setup requires the researcher to authenticate with an external
  service before the transfer endpoint can be created
- **THEN** the address to authenticate at is displayed, and setup waits for the researcher
  to supply what the external service returns

#### Scenario: Host that cannot open a browser

- **WHEN** setup would direct the researcher to a web address on a host where no browser
  can be launched
- **THEN** the address is still displayed in a form the researcher can copy elsewhere, and
  setup completes

#### Scenario: Setup already completed

- **WHEN** VISTA is launched on an installation whose transfer endpoint was set up on a
  previous run
- **THEN** no login is requested and the endpoint starts without researcher interaction

#### Scenario: Dependent tools report the cause

- **WHEN** a job tool that depends on file transfer is invoked after setup did not complete
- **THEN** it fails with a message naming the incomplete setup rather than an internal error

#### Scenario: The tool's remedy is one the researcher can perform

- **WHEN** a job tool reports that file-transfer setup is incomplete
- **THEN** the remedy it names is available in the researcher's installation, rather than a
  step that only a source checkout provides

## ADDED Requirements

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
