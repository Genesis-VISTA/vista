## ADDED Requirements

### Requirement: Releases record their data compatibility

Every release SHALL carry an `update.json` asset that records, as `data_compatible_from`, the
oldest earlier release whose local database the new release's startup check accepts. The
release workflow SHALL determine it by creating a database with each earlier release tag's
code, newest first, and running the new release's startup schema check against it, stopping
at the first one refused. When every earlier release is accepted, `data_compatible_from` SHALL
be the oldest release tag. A release whose compatibility could not be determined SHALL NOT be
drafted.

#### Scenario: An additive release

- **WHEN** 0.2.5 only adds nullable columns since 0.2.4, and 0.2.3 dropped a column from 0.2.2
- **THEN** 0.2.5's `update.json` records `data_compatible_from` as 0.2.3

#### Scenario: The check cannot run

- **WHEN** the compatibility step fails to create an earlier release's database
- **THEN** the workflow fails and no draft release is created
