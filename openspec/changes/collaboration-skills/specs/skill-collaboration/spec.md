## Purpose

How a researcher shares skills through the collaboration server: publishing one, finding the
server's skills on the Skill Hub, and importing them, with every step explicit.

## ADDED Requirements

### Requirement: A skill records where it stands with the collaboration server

Every skill SHALL record the collaboration server, item and version its files were published
as or imported from (none until then), whether it ships with VISTA (`bundled`), and whether its
files have changed here since that version (`modified`). Every write to a skill's files SHALL
set `modified`; a publish or an import SHALL clear it. A repository import SHALL record its
folder's URL as `repo_url`.

#### Scenario: An edit after publishing

- **GIVEN** a skill published as version 2
- **WHEN** the researcher changes its SKILL.md
- **THEN** it is modified, still recorded as version 2

#### Scenario: A bundled skill

- **WHEN** VISTA seeds its bundled skills
- **THEN** each is bundled and not modified

### Requirement: Publishing a skill

`POST /skills/{name}/publish` SHALL upload the skill's folder as a gzipped tar with a manifest
read from its SKILL.md, as version 1 of a new item of kind `skill` when the skill has none on
the server in effect, otherwise as the next version on top of the version it records. The
archive SHALL hold only the skill's regular files, within the folder upload's limits. A bundled
skill SHALL NOT be published.

#### Scenario: First publish

- **WHEN** the researcher publishes a skill never published
- **THEN** the server has a `skill` item with its version 1, and the skill records that item,
  version 1, and is not modified

#### Scenario: A bundled skill

- **WHEN** the researcher publishes a bundled skill
- **THEN** the backend answers `409` naming it as bundled, and nothing is sent

#### Scenario: No server

- **WHEN** no collaboration server answers
- **THEN** publishing answers `503` with the reason, and the Publish button is disabled with it

### Requirement: A stale publish lets the researcher choose

When the server's newest version is past the one the skill records, the publish SHALL change
nothing and answer `409` with `code` `version_conflict` and the newest version. The researcher
SHALL then be able to take the server's version, publish on top of the version shown, or
publish as a new skill. A skill whose item is gone from the server SHALL answer `409`
`remote_missing` and may be published as new.

#### Scenario: Someone published first

- **GIVEN** a skill recorded as version 1, modified here, whose item's head is version 2
- **WHEN** the researcher publishes
- **THEN** the answer is `409` with `head_version` 2, and the skill still records version 1

#### Scenario: Publish on top

- **WHEN** the researcher then publishes on top of version 2
- **THEN** the skill is the item's version 3

#### Scenario: Take the server's version

- **WHEN** the researcher instead takes the server's version
- **THEN** the skill's files are version 2's, and it records version 2, not modified

### Requirement: The Skill Hub lists the server's skills

`GET /collaboration/skills` SHALL list the server's `skill` items with their newest version's
manifest, who published it, this installation's copy of it if any, and whether another skill
here has its name. The Skill Hub page SHALL show them, with the HPC jobs each manifest names,
and SHALL list nothing and show the reason when no collaboration server answers.

#### Scenario: No server

- **WHEN** the researcher opens the Skill Hub with no collaboration server set
- **THEN** it shows the reason from the status and asks the backend for no listing

#### Scenario: A copy here that is behind

- **GIVEN** a skill imported as version 1 of an item whose head is version 2
- **THEN** its hub card offers "Update to version 2" and its `/skills` card says version 2 is
  available

### Requirement: Importing a skill

`POST /collaboration/skills/{id}/import` SHALL download a version (the newest by default),
check it against the version's digest, unpack it through the folder upload's checks, refusing
anything but files and folders or a path outside the skill, and load it into the given
project.

A skill here from the same item SHALL be updated in place, keeping its name; if it is
modified, the import SHALL answer `409` `modified` unless the researcher confirms replacing the
changes. Otherwise the new skill SHALL be named after the item, and a taken name SHALL answer
`409` `name_taken` until the researcher gives a new one. Executable files SHALL stay
executable.

#### Scenario: Import into the project

- **WHEN** the researcher imports a skill from the hub with a project active
- **THEN** the skill is on this computer, records the item and version, is not modified, and
  is loaded into the project

#### Scenario: A taken name

- **GIVEN** a different skill here named `salt-density`
- **WHEN** the researcher imports `salt-density` from the server and names it
  `salt-density-bea`
- **THEN** a skill `salt-density-bea`, whose SKILL.md says so, is imported and the other is
  unchanged

#### Scenario: Changes here

- **GIVEN** an imported skill modified here, behind the server
- **WHEN** the researcher updates it
- **THEN** they are told the changes will be replaced, and nothing changes until they confirm

#### Scenario: A link in the archive

- **WHEN** a version's archive holds a symlink
- **THEN** the import is refused and nothing is written

### Requirement: The local library

The Skills page SHALL list the skills loaded in the active project and the other skills on
this computer, with each one's state with the collaboration server. Unloading SHALL NOT delete a
skill; deleting SHALL be a separate, confirmed action that removes it from this computer only.
A bundled skill SHALL NOT be deleted.

#### Scenario: Unload

- **WHEN** the researcher unloads a skill
- **THEN** it leaves the project and stays on this computer, under the other skills

#### Scenario: A bundled skill

- **WHEN** the researcher tries to delete a bundled skill
- **THEN** the backend answers `409`, and the Skills page offers no Delete for it
