## ADDED Requirements

### Requirement: Remote directory filled in from the cluster's project

When the modal knows the project of Odo, Frontier or Lux, from the cluster status's credential
check (an S3M token's `project` claim for Odo and Frontier, the Lux account for Lux), it SHALL
fill that cluster's remote directory with the project's folder:

| Cluster | Folder |
|---|---|
| Odo | `/gpfs/wolf2/olcf/<folder>/proj-shared/vista` |
| Frontier, Lux | `/lustre/orion/<folder>/proj-shared/vista` |

`<folder>` SHALL be the project lowercased and cut at its first dash (`gen150-vista` gives
`gen150`).

It SHALL fill the field only when the field is empty, or when it holds that cluster's folder
for a different project. It SHALL never change any other value. It SHALL NOT fill a field
whose edit has not been saved yet. It SHALL do this whenever the modal has the project, both
after a token or account is saved and when the modal opens with one already saved. The fill
SHALL be saved, marked and announced like a typed change.

While the field holds the folder it filled for the current project, a note under it SHALL say
where the value came from:
- "Filled from your S3M token's project (gen150 folder inferred from gen150-vista)." when the
  folder differs from the project;
- "Filled from your S3M token's project, chm243." when it does not;
- "Filled from your Lux account, stf218." for Lux.

Before a project is known, each of the three clusters' hints SHALL begin "Filled from your S3M
token once you add one." ("…your Lux account…" for Lux). The placeholder SHALL stay as it is.
Removing a token SHALL leave the directory as it is.

#### Scenario: A token from a subproject

- **GIVEN** Odo's remote directory is empty
- **WHEN** a researcher saves an Odo token whose project is `gen150-vista`
- **THEN** the directory becomes `/gpfs/wolf2/olcf/gen150/proj-shared/vista`, is saved, and the
  note reads "Filled from your S3M token's project (gen150 folder inferred from gen150-vista)."

#### Scenario: A token from a project with no dash

- **GIVEN** Frontier's remote directory is empty
- **WHEN** a researcher saves a Frontier token whose project is `chm243`
- **THEN** the directory becomes `/lustre/orion/chm243/proj-shared/vista` and the note reads
  "Filled from your S3M token's project, chm243."

#### Scenario: A token from another project replaces a filled folder

- **GIVEN** Odo's remote directory is `/gpfs/wolf2/olcf/gen150/proj-shared/vista`
- **WHEN** a researcher saves an Odo token whose project is `stf218`
- **THEN** the directory becomes `/gpfs/wolf2/olcf/stf218/proj-shared/vista`

#### Scenario: A typed directory is kept

- **GIVEN** Odo's remote directory is `/gpfs/wolf2/olcf/gen150/proj-shared/sam/v2`
- **WHEN** a researcher saves an Odo token from any project
- **THEN** the directory is unchanged and no note is shown

#### Scenario: Editing a filled directory

- **GIVEN** Odo's remote directory was filled for `gen150-vista`
- **WHEN** the researcher changes it to another path
- **THEN** the note is no longer shown, and the new path is never replaced while the token's
  project stays the same

#### Scenario: Lux from its account

- **GIVEN** Lux's remote directory is empty
- **WHEN** a researcher saves the Lux account `stf218`
- **THEN** the directory becomes `/lustre/orion/stf218/proj-shared/vista` and the note reads
  "Filled from your Lux account, stf218."

#### Scenario: A token saved before this change

- **GIVEN** an Odo token was saved and the remote directory left empty before this change
- **WHEN** the researcher opens Odo's settings
- **THEN** the directory is filled from the token's project

#### Scenario: Removing the token

- **GIVEN** Odo's remote directory was filled from its token
- **WHEN** the researcher removes the token
- **THEN** the directory is unchanged
