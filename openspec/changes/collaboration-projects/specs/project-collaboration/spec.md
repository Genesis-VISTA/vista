## Purpose

How a researcher shares a project through the collaboration server: publishing it with the
skills it loads, finding the server's projects, and importing one with the skill versions it
names, with every step explicit.

## ADDED Requirements

### Requirement: A project records where it stands with the collaboration server

Every project SHALL record the collaboration server, item and version it was published as or
imported from (none until then), and whether its own fields have changed here since that
version (`modified`). Creating a project, a save that changes its fields, and loading a skill
into it SHALL set `modified`. A save that changes nothing SHALL NOT. A publish or an import
SHALL clear it.

#### Scenario: A save that changes nothing

- **GIVEN** a project published as version 1
- **WHEN** the researcher loads a skill that it already loads, or saves it unchanged
- **THEN** it is still version 1 and not modified

#### Scenario: An edit after publishing

- **GIVEN** a project published as version 1
- **WHEN** the researcher changes its system prompt
- **THEN** it is modified, still recorded as version 1

### Requirement: Publishing a project

`POST /projects/{name}/publish` SHALL publish the project as version 1 of a new item of kind
`project`, or as the next version of its item on the server in effect. Its manifest SHALL be
built from the project's fields by name: name, description, system prompt, tools, usage
limits and forum URL, the skills it loads as references, and its knowledge bases by slug. It
SHALL NOT contain the project's id or collaboration state.

#### Scenario: Nothing new

- **WHEN** the researcher publishes a project unchanged since its version, whose skills are
  unchanged too
- **THEN** no version is published, and the answer says it is unchanged

#### Scenario: No server

- **WHEN** no collaboration server answers
- **THEN** publishing answers `503` with the reason, and the Publish button is disabled with it

### Requirement: A project's skills are published first

Before the project, each skill it loads that is not on the server in effect, or has changed
since its version, SHALL be published. A skill bundled with VISTA SHALL be referenced as
bundled and not published. A loaded skill that is not on this computer SHALL be referenced
as bundled when the version the publish builds on referenced it as bundled, and SHALL
otherwise stop the publish.

#### Scenario: First publish

- **GIVEN** a project that loads a skill never published and a bundled skill
- **WHEN** the researcher publishes the project
- **THEN** the skill is published as version 1 first
- **AND** the project is version 1 of a `project` item whose manifest references that skill
  version and the bundled skill by name
- **AND** the project records the item and version 1, and is not modified

#### Scenario: A skill changed

- **GIVEN** a published project whose own fields are unchanged
- **WHEN** a skill it loads was edited, and the researcher publishes the project
- **THEN** the skill's next version is published, and then the project's next version, which
  references it

#### Scenario: A skill not on this computer

- **WHEN** the project loads a skill that is not here
- **THEN** the publish answers `400` naming it, and nothing is published

### Requirement: A stale publish lets the researcher choose

When the server's newest version of the project is past the one it records, the publish
SHALL answer `409` with `code` `version_conflict` and the newest version before any skill is
published. The researcher SHALL then be able to take the server's version, publish on top of
the version shown, or publish as a new project. A project whose item is gone SHALL answer
`409` `remote_missing`.

#### Scenario: Someone published first

- **GIVEN** a project recorded as version 1 whose item's head is version 2, and a new skill
  loaded into it
- **WHEN** the researcher publishes
- **THEN** the answer is `409` with `head_version` 2, and the skill is not published

#### Scenario: Publish on top

- **WHEN** the researcher then publishes on top of version 2
- **THEN** the project is its item's version 3

### Requirement: A skill that needs a choice stops the project's publish

A loaded skill whose publish needs the researcher's choice SHALL stop the project's publish
with `409` `skill_conflict` naming the skill, and no project version SHALL be published.

#### Scenario: A skill's conflict

- **GIVEN** a loaded skill whose own item has moved past the version it records
- **WHEN** the researcher publishes the project
- **THEN** the answer is `409` `skill_conflict` naming the skill
- **AND** the projects page points to the Skills page to resolve it

### Requirement: The server's projects can be listed

`GET /collaboration/projects` SHALL list the server's `project` items. Each entry SHALL have
its newest version's manifest, who published it, this installation's copy if any, and
whether another project here has its name. The projects page SHALL list them in an import
dialog, and SHALL disable the dialog's button, showing the reason, when no collaboration
server answers.

#### Scenario: A copy here that is behind

- **GIVEN** a project imported as version 1 of an item whose head is version 2
- **THEN** its entry offers "Update to version 2", and its card says version 2 is available

### Requirement: Importing a project

`POST /collaboration/projects/{id}/import` SHALL import a version (the newest by default),
creating the project with an id of its own or updating the copy from the same item in place,
keeping its id and name. Before importing anything it SHALL refuse a copy here with changes
made here (`409` `modified`, until the researcher confirms) and a different project here with
the name (`409` `name_taken`, until the researcher gives a new one).

#### Scenario: A version VISTA cannot store

- **WHEN** a version's fields do not validate (its usage limits, say)
- **THEN** the import is refused before any skill is imported

#### Scenario: Never another installation's id

- **WHEN** a version's manifest carries the id of a project here
- **THEN** the imported project has a new id, and the project here is unchanged

#### Scenario: A taken name

- **GIVEN** a different project here with the name
- **WHEN** the researcher imports the project
- **THEN** the answer is `409` `name_taken` and no skill is imported
- **AND** importing it with a new name succeeds

#### Scenario: Changes here

- **GIVEN** a project imported as version 2 and changed here, whose item's head is version 3
- **WHEN** the researcher imports version 3
- **THEN** the answer is `409` `modified` and nothing changes until they confirm

### Requirement: An import brings the project's skills

An import SHALL import the referenced skill versions that are not here, and the project SHALL
load them. It SHALL keep every knowledge base slug and every bundled skill name the version
names, whether or not this computer has them, unless a different skill here has the name. The
answer SHALL say what happened to each skill. A skill's own conflict SHALL stop the import with
`409` `skill_conflict` naming the skill.

#### Scenario: A skill that needs a choice

- **WHEN** a referenced skill's import needs the researcher's choice
- **THEN** the answer is `409` `skill_conflict` naming the skill, not the project's `modified`
  or `name_taken`

#### Scenario: Import with its skills

- **GIVEN** a project on the server that loads a skill version not here and a bundled skill
- **WHEN** the researcher imports it
- **THEN** the skill is imported, recording its item and version
- **AND** the project is created loading both, recording its item and version, not modified

#### Scenario: Skills already here

- **GIVEN** copies here of the referenced skills: one older and unchanged, one with changes
  made here, and one newer
- **WHEN** the researcher imports the project
- **THEN** the older copy is updated to the referenced version
- **AND** the other two are kept as they are, and the answer says so
- **AND** the project is not modified: the kept copies are the skills' own state

#### Scenario: A bundled skill shadowed here

- **GIVEN** a different, non-bundled skill here with the name of a bundled skill the version
  names
- **WHEN** the researcher imports the project
- **THEN** the project does not load that skill, and is modified

#### Scenario: Names from the manifest

- **WHEN** a version names a skill reference that is not a skill name (a path, say)
- **THEN** it is not imported, and listed without a manifest
- **WHEN** the item's name is not one a project here may have
- **THEN** the answer is `409` `name_invalid`, and the dialog asks for one

#### Scenario: A bundled skill of another VISTA, published from here

- **GIVEN** an imported project naming a bundled skill this VISTA does not have
- **WHEN** the researcher edits the project and publishes it
- **THEN** the new version references that skill as bundled

#### Scenario: What this computer lacks is carried

- **GIVEN** a version naming a knowledge base and a bundled skill this computer does not have
- **WHEN** the researcher imports it, renamed, and then publishes it unchanged
- **THEN** the project still names both, and no new version is published

#### Scenario: A skill name taken by another skill

- **GIVEN** a different skill here named `vllm-lux`
- **WHEN** a project loading a skill item named `vllm-lux` is imported
- **THEN** that skill is imported under the first free name, `vllm-lux-2` or after, and the
  project loads it by that name

### Requirement: An unreachable forum does not fail an import

An imported project's Hypothesis Lab SHALL be set up as a save would set it up. If the forum
cannot be reached or set up, the import SHALL still succeed: the project is kept with its
forum URL, and the answer gives the reason in `forum_error`.

#### Scenario: A forum this computer cannot reach

- **WHEN** a project whose forum URL does not resolve here is imported
- **THEN** the project is imported, its forum URL kept, and the answer carries the reason
