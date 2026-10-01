# default-projects Specification

## Purpose
Defines which projects and knowledge bases VISTA provides out of the box, how the optional
science projects are switched on, and how defaults reach databases that already exist.

## Requirements

### Requirement: AI Safety in Autonomous Labs is the default project

VISTA SHALL seed a project named `ai-safety-autonomous-labs`, described as AI Safety in
Autonomous Labs, on every installation regardless of configuration. The project SHALL have
its own system prompt framing its literature corpus, no mandated skills, a per-turn request
limit of 50, and access to every tool except the retired `agenthpc_*` tools, so that HPC
job submission is available to it.

#### Scenario: Fresh installation with no configuration

- **WHEN** VISTA starts for the first time with no VISTA environment variables set
- **THEN** the project list contains `ai-safety-autonomous-labs`

#### Scenario: HPC jobs are available to the project

- **WHEN** a researcher in `ai-safety-autonomous-labs` asks the agent which HPC jobs it can
  run
- **THEN** the agent can list and submit the jobs VISTA provides, without any job being
  named in the project's configuration

### Requirement: The default project has its own knowledge base

VISTA SHALL provide a knowledge base with slug `ai-safety`, named "AI Safety Papers",
containing exactly the documents in vista-data's `ai-safety/` folder, and SHALL attach it
to `ai-safety-autonomous-labs`. When vista-data is not available, the project SHALL still be
seeded, without the knowledge base and with literature search unavailable to it, and a
warning SHALL be logged naming the missing corpus.

#### Scenario: Retrieval over the AI-safety corpus

- **WHEN** vista-data is available at seeding and a researcher in
  `ai-safety-autonomous-labs` asks about security risks of autonomous agents
- **THEN** the agent's literature search returns passages cited from the `ai-safety`
  knowledge base

#### Scenario: No vista-data access

- **WHEN** VISTA is seeded with neither a vista-data token nor a bundled payload
- **THEN** `ai-safety-autonomous-labs` exists with no knowledge base, literature search is
  not offered to it, startup succeeds, and the log warns that the AI-safety corpus was
  unavailable

### Requirement: Science projects are opt-in

The `molten-salt` and `alloy-design` projects, the `molten-salt-papers` knowledge base and
the MSTDB-derived assets SHALL be seeded from a vista-data token only when the
`VISTA_BACKEND_SEED_SCIENCE_PROJECTS` setting is true. It SHALL default to false. Bundled
skills SHALL be registered in the skill library regardless of the setting, except skills
whose required MSTDB assets are unavailable, which SHALL be skipped.

#### Scenario: Default development seeding

- **WHEN** a development database is seeded with a vista-data token and the setting unset
- **THEN** only `ai-safety-autonomous-labs` is created, no molten-salt corpus or MSTDB file
  is fetched, and every bundled skill that does not need MSTDB is in the skill library

#### Scenario: Science projects switched on

- **WHEN** a development database is seeded with a vista-data token and
  `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true`
- **THEN** `ai-safety-autonomous-labs`, `molten-salt` and `alloy-design` are created,
  `molten-salt` has the `molten-salt-papers` knowledge base, and the MSTDB-dependent skills
  are registered

### Requirement: Payload seeding follows the payload's contents

When seeding from a bundled payload, VISTA SHALL seed what the payload contains and SHALL
NOT consult the science-projects setting. The AI-safety knowledge base SHALL be seeded when
the payload contains `ai-safety/`. The science projects, their knowledge base and the
MSTDB-dependent skills SHALL be seeded only when it contains both `molten-salt-papers/` and
`mstdb/`. A payload lacking the science data SHALL NOT cause seeding to fail.

#### Scenario: Default package payload

- **WHEN** a package whose payload holds only `ai-safety/` is started for the first time
- **THEN** `ai-safety-autonomous-labs` is seeded with its knowledge base, no science project
  is seeded, and seeding completes without error

#### Scenario: Science package payload

- **WHEN** a package whose payload holds `ai-safety/`, `molten-salt-papers/` and `mstdb/` is
  started for the first time
- **THEN** all three projects and both knowledge bases are seeded

### Requirement: Defaults reach existing databases additively

On every startup VISTA SHALL insert `ai-safety-autonomous-labs` and, when its corpus is
available, the `ai-safety` knowledge base if either is missing from the database,
identified by a fixed identity rather than by name. The science projects SHALL be seeded
only into an empty database, as before, and SHALL NOT be added to an existing one. Once the
`ai-safety` knowledge base exists, VISTA SHALL attach it to `ai-safety-autonomous-labs` if it
is not already attached. This SHALL NOT overwrite any other field of an existing project or
knowledge base, SHALL NOT remove any knowledge base a user attached, and SHALL NOT delete
any project, knowledge base or data, including legacy science projects.

#### Scenario: Upgrading a database seeded before this change

- **WHEN** VISTA starts against a database that holds `molten-salt` and `alloy-design` but
  no `ai-safety-autonomous-labs`, with the AI-safety corpus available
- **THEN** `ai-safety-autonomous-labs` and the `ai-safety` knowledge base are added, and
  `molten-salt` and `alloy-design` are unchanged

#### Scenario: Corpus becomes available later

- **WHEN** `ai-safety-autonomous-labs` was seeded without its knowledge base and VISTA later
  starts with the AI-safety corpus available
- **THEN** the `ai-safety` knowledge base is created and attached to the project, and the
  project's other settings are unchanged

#### Scenario: User edits survive

- **WHEN** a researcher has edited `ai-safety-autonomous-labs`' system prompt and VISTA
  restarts
- **THEN** the edited system prompt is retained

#### Scenario: Unreachable vista-data does not block startup

- **WHEN** VISTA starts with a vista-data token, the `ai-safety` knowledge base is missing,
  and vista-data cannot be reached
- **THEN** startup completes, the project remains without its knowledge base, and a warning
  is logged; the next startup tries again

#### Scenario: Science projects are not added to an existing database

- **WHEN** VISTA starts against an existing database with no science projects and
  `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true`
- **THEN** no science project is added

#### Scenario: A deleted default is restored

- **WHEN** a researcher deletes `ai-safety-autonomous-labs` and VISTA restarts
- **THEN** the project is present again with its default settings
