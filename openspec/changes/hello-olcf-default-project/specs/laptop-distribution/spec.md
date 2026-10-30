## MODIFIED Requirements

### Requirement: Bundled corpus is searchable at first run

The artifact SHALL include the OLCF docs corpus already indexed, so retrieval works on
first run without an indexing step. A knowledge base SHALL NOT be presented as ready
unless its index actually contains content. The molten-salt corpus, its index and MSTDB
data SHALL be included only in an artifact built with the science projects enabled; an
artifact built without them SHALL contain none of that data.

#### Scenario: Retrieval works immediately

- **WHEN** a researcher asks a question on their first session after installation
- **THEN** retrieval returns passages from the bundled OLCF docs corpus with no indexing
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

The build SHALL verify its own prerequisites before producing an artifact, and SHALL verify the
finished artifact before it is considered complete. Verification SHALL include starting the
artifact from a location other than the one it was built in, and SHALL include the supervised
startup protocol the application depends on. Verification SHALL NOT require code signing.

#### Scenario: A build prerequisite is missing

- **WHEN** a build is started without one of its required credentials or tools, or without the
  OLCF docs corpus in its source data
- **THEN** it fails immediately, naming what is missing, rather than producing an incomplete
  artifact

#### Scenario: Science data required only when enabled

- **WHEN** a build is started without the science projects enabled and its source data lacks the
  molten-salt corpus or MSTDB
- **THEN** the build proceeds

#### Scenario: A reused index that does not match its corpus

- **WHEN** a build reuses a prebuilt index that cites a document, Markdown or PDF, missing from
  the corpus in its source data
- **THEN** it fails, naming the first missing documents, rather than shipping citations that
  cannot be opened

#### Scenario: The finished artifact is exercised

- **WHEN** a build completes
- **THEN** the artifact is unpacked to a different location, started, checked for service health,
  checked with one retrieval query against the OLCF docs corpus, checked to contain no science
  data unless it was built with the science projects enabled, and shut down before the build
  reports success

#### Scenario: The supervised protocol is exercised

- **WHEN** a build completes, on any platform
- **THEN** the relocated artifact's launcher is run in supervised mode without a window, its events
  are checked to arrive in order through to UI readiness with the interface's address, a deliberate
  port conflict is checked to be reported as a preflight failure with its code, and a stop request
  is checked to leave no process running and no port held

#### Scenario: Contents are recorded

- **WHEN** a build completes
- **THEN** it records the components included and their sizes, and each platform's application
  entry point and diagnostic launcher, so an incomplete artifact can be identified without
  unpacking it

### Requirement: Upgraded installations receive new bundled corpora

When a newer artifact is run against an existing state directory, first-run setup SHALL
install any bundled corpus the state directory does not already have, even when other
bundled corpora are already installed. It SHALL NOT replace or remove a corpus already
installed.

#### Scenario: Upgrading from a molten-salt package

- **WHEN** a package containing the OLCF docs corpus is run against a state directory
  created by an earlier package that installed only the molten-salt corpus
- **THEN** the OLCF docs corpus and its index are installed, the molten-salt corpus is left
  in place, and `hello-olcf`'s literature search works in that session

#### Scenario: Upgrading from an AI-safety package

- **WHEN** a package containing the OLCF docs corpus is run against a state directory
  created by an earlier package that installed only the AI-safety corpus
- **THEN** the OLCF docs corpus and its index are installed, the AI-safety corpus is left
  in place, and `hello-olcf`'s literature search works in that session

#### Scenario: Nothing new to install

- **WHEN** a package is run against a state directory that already holds every corpus it
  bundles
- **THEN** no corpus is extracted or overwritten
