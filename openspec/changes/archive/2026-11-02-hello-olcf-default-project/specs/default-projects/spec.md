## ADDED Requirements

### Requirement: hello-olcf is the default project

VISTA SHALL seed a project named `hello-olcf` on every installation regardless of
configuration. The project SHALL load the skills `hello-odo`, `hello-frontier`, `hello-lux`
and `hpc-skill-author`, SHALL have a per-turn request limit of 50, and SHALL have access to
every tool except the retired `agenthpc_*` tools. Its system prompt SHALL tell the agent to
answer OLCF questions from the `olcf-docs` knowledge base, to give the docs.olcf.ornl.gov URL
of every OLCF passage it cites (reading the cited file for its URL when a search result does
not show one), to offer a hello run on a connected cluster, and to offer to author a job.

#### Scenario: Fresh installation with no configuration

- **WHEN** VISTA starts for the first time with no VISTA environment variables set
- **THEN** the project list contains `hello-olcf`, and it loads the three hello skills and
  `hpc-skill-author`

#### Scenario: A cited OLCF answer

- **WHEN** a researcher in `hello-olcf` asks how to request GPUs for a job on Frontier
- **THEN** the answer cites passages from the `olcf-docs` knowledge base, each with its
  docs.olcf.ornl.gov URL

#### Scenario: HPC jobs are available to the project

- **WHEN** a researcher in `hello-olcf` asks the agent to run hello-world on a cluster
- **THEN** the agent can submit that cluster's hello job without any job being named in the
  project's configuration

### Requirement: The default project has the OLCF docs knowledge base

VISTA SHALL provide a knowledge base with slug `olcf-docs`, named "OLCF User
Documentation", containing exactly the documents in vista-data's `olcf-docs/` folder, and
SHALL attach it to `hello-olcf`. When vista-data is not available, the project SHALL still
be seeded, without the knowledge base and with literature search unavailable to it, and a
warning SHALL be logged naming the missing corpus.

#### Scenario: Retrieval over the OLCF docs

- **WHEN** vista-data is available at seeding and a researcher in `hello-olcf` asks about
  Frontier's batch queues
- **THEN** the agent's literature search returns passages cited from the `olcf-docs`
  knowledge base

#### Scenario: No vista-data access

- **WHEN** VISTA is seeded with neither a vista-data token nor a bundled payload
- **THEN** `hello-olcf` exists with no knowledge base, literature search is not offered to
  it, startup succeeds, and the log warns that the OLCF docs corpus was unavailable

### Requirement: The AI-safety project is no longer seeded

VISTA SHALL NOT seed the `ai-safety-autonomous-labs` project or the `ai-safety` knowledge
base on any installation, new or existing. A copy that an existing database already holds
SHALL be left unchanged, SHALL keep working, and SHALL NOT be restored once deleted.

#### Scenario: Fresh installation

- **WHEN** VISTA starts for the first time
- **THEN** the project list contains no `ai-safety-autonomous-labs` and there is no
  `ai-safety` knowledge base

#### Scenario: Upgrading a database that holds the AI-safety project

- **WHEN** VISTA starts against a database that holds `ai-safety-autonomous-labs` and the
  `ai-safety` knowledge base
- **THEN** both are unchanged, and `hello-olcf` is added beside them

#### Scenario: Deleting the old project

- **WHEN** a researcher deletes `ai-safety-autonomous-labs` and VISTA restarts
- **THEN** it is not recreated

## MODIFIED Requirements

### Requirement: Science projects are opt-in

The `molten-salt` and `alloy-design` projects, the `molten-salt-papers` knowledge base and
the MSTDB-derived assets SHALL be seeded from a vista-data token only when the
`VISTA_BACKEND_SEED_SCIENCE_PROJECTS` setting is true. It SHALL default to false. Bundled
skills SHALL be registered in the skill library regardless of the setting, except skills
whose required MSTDB assets are unavailable, which SHALL be skipped.

#### Scenario: Default development seeding

- **WHEN** a development database is seeded with a vista-data token and the setting unset
- **THEN** only `hello-olcf` is created, no molten-salt corpus or MSTDB file is fetched, and
  every bundled skill that does not need MSTDB is in the skill library

#### Scenario: Science projects switched on

- **WHEN** a development database is seeded with a vista-data token and
  `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true`
- **THEN** `hello-olcf`, `molten-salt` and `alloy-design` are created, `molten-salt` has the
  `molten-salt-papers` knowledge base, and the MSTDB-dependent skills are registered

### Requirement: Payload seeding follows the payload's contents

When seeding from a bundled payload, VISTA SHALL seed what the payload contains and SHALL
NOT consult the science-projects setting. The `olcf-docs` knowledge base SHALL be seeded
when the payload contains `olcf-docs/`. The science projects, their knowledge base and the
MSTDB-dependent skills SHALL be seeded only when it contains both `molten-salt-papers/` and
`mstdb/`. A payload lacking the science data SHALL NOT cause seeding to fail.

#### Scenario: Default package payload

- **WHEN** a package whose payload holds only `olcf-docs/` is started for the first time
- **THEN** `hello-olcf` is seeded with its knowledge base, no science project is seeded, and
  seeding completes without error

#### Scenario: Science package payload

- **WHEN** a package whose payload holds `olcf-docs/`, `molten-salt-papers/` and `mstdb/`
  is started for the first time
- **THEN** all three projects and both knowledge bases are seeded

### Requirement: Defaults reach existing databases additively

On every startup VISTA SHALL insert `hello-olcf` and, when its corpus is available, the
`olcf-docs` knowledge base if either is missing from the database, identified by a fixed
identity rather than by name. The science projects SHALL be seeded only into an empty
database, as before, and SHALL NOT be added to an existing one. Once the `olcf-docs`
knowledge base exists, VISTA SHALL attach it to `hello-olcf` if it is not already attached.
This SHALL NOT overwrite any other field of an existing project or knowledge base, SHALL NOT
remove any knowledge base a user attached, and SHALL NOT delete any project, knowledge base
or data, including legacy science projects and the AI-safety project.

#### Scenario: Upgrading a database seeded before this change

- **WHEN** VISTA starts against a database that holds `ai-safety-autonomous-labs` but no
  `hello-olcf`, with the OLCF docs corpus available
- **THEN** `hello-olcf` and the `olcf-docs` knowledge base are added, and
  `ai-safety-autonomous-labs` is unchanged

#### Scenario: Corpus becomes available later

- **WHEN** `hello-olcf` was seeded without its knowledge base and VISTA later starts with
  the OLCF docs corpus available
- **THEN** the `olcf-docs` knowledge base is created and attached to the project, and the
  project's other settings are unchanged

#### Scenario: User edits survive

- **WHEN** a researcher has edited `hello-olcf`'s system prompt and VISTA restarts
- **THEN** the edited system prompt is retained

#### Scenario: Unreachable vista-data does not block startup

- **WHEN** VISTA starts with a vista-data token, the `olcf-docs` knowledge base is missing,
  and vista-data cannot be reached
- **THEN** startup completes, the project remains without its knowledge base, and a warning
  is logged; the next startup tries again

#### Scenario: Science projects are not added to an existing database

- **WHEN** VISTA starts against an existing database with no science projects and
  `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true`
- **THEN** no science project is added

#### Scenario: A deleted default is restored

- **WHEN** a researcher deletes `hello-olcf` and VISTA restarts
- **THEN** the project is present again with its default settings

## REMOVED Requirements

### Requirement: AI Safety in Autonomous Labs is the default project

**Reason**: `hello-olcf` replaces it as the default project.
**Migration**: New installs get `hello-olcf`. Existing installs keep their
`ai-safety-autonomous-labs` copy unchanged, and it is no longer restored if deleted (see "The
AI-safety project is no longer seeded").

### Requirement: The default project has its own knowledge base

**Reason**: The default project's knowledge base becomes `olcf-docs`.
**Migration**: See "The default project has the OLCF docs knowledge base". An existing
`ai-safety` knowledge base is left in place.
