## MODIFIED Requirements

### Requirement: Bundled corpus is searchable at first run

The artifact SHALL include the AI-safety corpus already indexed, so retrieval works on
first run without an indexing step. A knowledge base SHALL NOT be presented as ready
unless its index actually contains content. The molten-salt corpus, its index and MSTDB
data SHALL be included only in an artifact built with the science projects enabled; an
artifact built without them SHALL contain none of that data.

#### Scenario: Retrieval works immediately

- **WHEN** a researcher asks a question on their first session after installation
- **THEN** retrieval returns passages from the bundled AI-safety corpus with no indexing
  having run

#### Scenario: Cited sources are readable

- **WHEN** retrieval cites a document from a bundled corpus
- **THEN** that document can be opened from the interface

#### Scenario: Missing or empty index is refused

- **WHEN** a bundled index is absent or contains no entries at first run
- **THEN** setup fails with a message identifying the incomplete corpus, rather than
  creating a knowledge base that reports itself ready and returns nothing

#### Scenario: The installed artifact never indexes

- **WHEN** an installed artifact starts, on first run or later, including when a bundled
  index is missing
- **THEN** no corpus is embedded and no citation-extraction call is made on the
  researcher's machine; a missing index fails setup as above

#### Scenario: Default artifact carries no science data

- **WHEN** an artifact is built without the science projects enabled
- **THEN** it contains no molten-salt corpus, no molten-salt index and no MSTDB file,
  including inside the HPC job catalog

#### Scenario: Science artifact

- **WHEN** an artifact is built with the science projects enabled
- **THEN** it also contains the molten-salt corpus already indexed and the MSTDB data, and
  retrieval against the molten-salt corpus works on first run

### Requirement: Build-time verification

The build SHALL verify its own prerequisites before producing an artifact, and SHALL verify
the finished artifact before it is considered complete. Verification SHALL include starting
the artifact from a location other than the one it was built in.

#### Scenario: A build prerequisite is missing

- **WHEN** a build is started without one of its required credentials or tools, or without
  the AI-safety corpus in its source data
- **THEN** it fails immediately, naming what is missing, rather than producing an
  incomplete artifact

#### Scenario: Science data required only when enabled

- **WHEN** a build is started without the science projects enabled and its source data
  lacks the molten-salt corpus or MSTDB
- **THEN** the build proceeds

#### Scenario: The finished artifact is exercised

- **WHEN** a build completes
- **THEN** the artifact is unpacked to a different location, started, checked for service
  health, checked with one retrieval query against the AI-safety corpus, checked to contain
  no science data unless it was built with the science projects enabled, and shut down
  before the build reports success

#### Scenario: Contents are recorded

- **WHEN** a build completes
- **THEN** it records the components included and their sizes, so an incomplete artifact
  can be identified without unpacking it

## ADDED Requirements

### Requirement: Upgraded installations receive new bundled corpora

When a newer artifact is run against an existing state directory, first-run setup SHALL
install any bundled corpus the state directory does not already have, even when other
bundled corpora are already installed. It SHALL NOT replace or remove a corpus already
installed.

#### Scenario: Upgrading from a molten-salt package

- **WHEN** a package containing the AI-safety corpus is run against a state directory
  created by an earlier package that installed only the molten-salt corpus
- **THEN** the AI-safety corpus and its index are installed, the molten-salt corpus is left
  in place, and the AI-safety project's literature search works in that session

#### Scenario: Nothing new to install

- **WHEN** a package is run against a state directory that already holds every corpus it
  bundles
- **THEN** no corpus is extracted or overwritten
