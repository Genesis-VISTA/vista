## MODIFIED Requirements

### Requirement: Mutable state lives outside the artifact

All state the running application creates or modifies SHALL live outside the unpacked
artifact. The artifact's own contents SHALL be treated as read-only at run time.

#### Scenario: Copying carries no personal data

- **WHEN** an artifact that has been run is copied to another machine
- **THEN** the recipient's instance contains none of the sender's conversations, uploaded
  files, or configured credentials

#### Scenario: Replacing the artifact preserves state

- **WHEN** the unpacked artifact is replaced with a newer version whose database is compatible
  with the existing one, and started
- **THEN** existing conversations, uploaded files, and configured credentials remain
  available

#### Scenario: Replacing the artifact with an incompatible version

- **WHEN** the unpacked artifact is replaced with a newer version that cannot use the existing
  database, and started
- **THEN** startup stops before changing the database, and the startup window offers to move
  the local data into a backup folder and start fresh, without the researcher moving or
  deleting files by hand
