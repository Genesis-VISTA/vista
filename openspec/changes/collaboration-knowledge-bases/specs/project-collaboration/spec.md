## MODIFIED Requirements

### Requirement: Publishing a project

`POST /projects/{name}/publish` SHALL publish the project as version 1 of a new item of kind
`project`, or as the next version of its item on the server in effect. Its manifest (format 2)
SHALL be built from the project's fields by name: name, description, system prompt, tools,
usage limits and forum URL, the skills it loads and the knowledge bases it searches as
references. It SHALL NOT contain the project's id or collaboration state.

#### Scenario: Nothing new

- **WHEN** the researcher publishes a project unchanged since its version, whose skills and
  knowledge bases are unchanged too
- **THEN** no version is published, and the answer says it is unchanged

#### Scenario: No server

- **WHEN** no collaboration server answers
- **THEN** publishing answers `503` with the reason, and the Publish button is disabled with it

### Requirement: An import brings the project's skills

An import SHALL import the referenced skill versions that are not here, and the project SHALL
load them. It SHALL keep every bundled skill name the version names, whether or not this
computer has it, unless a different skill here has the name. The answer SHALL say what
happened to each skill. A skill's own conflict SHALL stop the import with `409`
`skill_conflict` naming the skill.

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

- **GIVEN** a version naming a bundled skill this computer does not have, and a knowledge base
- **WHEN** the researcher imports it, renamed, and then publishes it unchanged
- **THEN** the project still names both, and no new version is published

#### Scenario: A skill name taken by another skill

- **GIVEN** a different skill here named `vllm-lux`
- **WHEN** a project loading a skill item named `vllm-lux` is imported
- **THEN** that skill is imported under the first free name, `vllm-lux-2` or after, and the
  project loads it by that name

## ADDED Requirements

### Requirement: A project's knowledge bases are published first

Before the project, and after its skills, each knowledge base it searches that is not on the
server in effect, or has changed since its version, SHALL be published with its PDFs and
index. A knowledge base's conflict SHALL stop the publish with `409`
`knowledge_base_conflict` naming it, and the page SHALL point to the Knowledge Bases page.

#### Scenario: First publish

- **GIVEN** a project that searches a knowledge base never published
- **WHEN** the researcher publishes the project
- **THEN** the knowledge base is published as version 1 first, and the project's manifest
  references that item and version

#### Scenario: A knowledge base changed

- **GIVEN** a published project whose knowledge base was renamed since
- **WHEN** the researcher publishes the project
- **THEN** the knowledge base's version 2 is published, then the project's version 2

### Requirement: A project's knowledge bases are checked before anything is published

Before anything is published, a project SHALL be refused that searches a knowledge base that
is indexing (`409`), or one not on this computer that the version the publish builds on did
not reference (`400`). One that version referenced SHALL be referenced as it did.

#### Scenario: A knowledge base not on this computer

- **WHEN** the researcher publishes a project naming a knowledge base this computer lacks and
  no earlier version referenced
- **THEN** the answer is `400` naming it, and no skill or knowledge base is published

#### Scenario: A knowledge base deleted here

- **GIVEN** an imported project whose knowledge base was deleted here
- **WHEN** the researcher edits and publishes the project
- **THEN** the new version references the knowledge base version the imported one did

### Requirement: An import brings the project's knowledge bases

An import SHALL import the referenced knowledge base versions that are not here, under the
reference's slug or, when that is taken, the first free `<slug>-<n>`, update an older
unchanged copy, and keep a newer copy or one with changes made here, which does not make the
project modified. The project SHALL search them by their slugs here, and the answer SHALL say
what happened to each. A knowledge base's own conflict SHALL stop the import with `409`
`knowledge_base_conflict` naming it.

#### Scenario: Import with its knowledge base

- **GIVEN** a project searching a knowledge base version not here, and a different knowledge
  base here with its slug
- **WHEN** the researcher imports the project
- **THEN** the knowledge base is imported as `<slug>-2`, recording its item and version, and
  the project searches `<slug>-2`

### Requirement: An import checks the project's knowledge bases first

Before importing any skill, an import SHALL decide what to do about each referenced knowledge
base, and SHALL refuse the whole import if one it would download is an index this VISTA cannot
search (`409`, naming the project), a copy it would update is indexing (`409`), or what it would
download does not fit on the disk (`507`).

#### Scenario: An index this VISTA cannot search

- **GIVEN** a project searching a knowledge base indexed with another embedding model
- **WHEN** the researcher imports the project
- **THEN** the answer is `409`, and no skill, knowledge base or project is created

#### Scenario: Not enough disk

- **WHEN** the knowledge bases a project would download do not fit in the free space
- **THEN** the answer is `507`, and nothing is imported

#### Scenario: A newer copy here

- **GIVEN** a copy here at version 2 of a knowledge base the project references at version 1
- **WHEN** the researcher imports the project
- **THEN** the copy is kept and used, the answer says so, and the project is not modified
