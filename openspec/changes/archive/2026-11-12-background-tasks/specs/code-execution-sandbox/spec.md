## ADDED Requirements

### Requirement: A command can outlive the call that started it

A caller SHALL be able to start a command in the sandbox that keeps running after the call
that started it has returned, with its output written to a file inside the sandbox. The
caller SHALL later be able to learn whether the command is still running and, once it has
ended, its exit status. A command started this way SHALL end when its sandbox is closed.

#### Scenario: Command continues after the call

- **WHEN** a caller starts `sleep 30; echo done` to outlive the call, and the call returns
- **THEN** the command is still running in the sandbox
- **AND** thirty seconds later its output file contains `done` and its exit status is 0

#### Scenario: Sandbox closes

- **WHEN** a sandbox is closed while such a command is running
- **THEN** the command does not continue

### Requirement: Stopping a command ends everything it started

Stopping a command that outlived its call SHALL end the command and every process it started,
on every supported sandbox backend, and SHALL NOT end any other command in the sandbox.

#### Scenario: Command with children

- **WHEN** a caller stops a command that has started child and grandchild processes
- **THEN** none of those processes is running afterwards

#### Scenario: Other commands are unaffected

- **WHEN** two such commands are running and the caller stops one
- **THEN** the other is still running
