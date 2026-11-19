## MODIFIED Requirements

### Requirement: Sandbox volumes

Host directories mounted into the sandbox SHALL appear at their configured sandbox path, with the configured access mode, on every supported host. Writes to a writable mount SHALL be visible on the host. A read-only mount SHALL refuse every write from inside the sandbox. A mount's host directory SHALL be created when missing only for a directory VISTA owns (the project volume and the folders inside it); a missing host directory VISTA does not own SHALL NOT be created, and SHALL NOT be mounted.

#### Scenario: Writable mount round-trip

- **WHEN** a file is written on the host into a writable mount, and another is written from inside the sandbox
- **THEN** each side sees the other's file

#### Scenario: Read-only mount refuses writes

- **WHEN** a host directory holding `a.txt` is mounted read-only, and the sandbox reads `a.txt`, then tries to create, change and delete files in the mount
- **THEN** the read returns the host file's contents, every write fails, and the host directory is unchanged

#### Scenario: A missing folder VISTA does not own is not created

- **WHEN** a sandbox is started with a read-only mount of a host directory that does not exist and that VISTA does not own
- **THEN** the sandbox starts without that mount, and the host directory still does not exist
