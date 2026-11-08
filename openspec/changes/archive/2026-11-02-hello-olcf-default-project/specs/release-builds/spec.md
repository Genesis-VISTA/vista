## MODIFIED Requirements

### Requirement: Release inputs are pinned

Everything a release build takes from outside the repository SHALL be fixed by an
identifier recorded in the repository: the OLCF docs corpus with its prebuilt vector store,
by a commit of the private build-inputs repository, and the embedding weights, by revision.
The build inputs SHALL record the `olcf/olcf-user-docs` commit their corpus was converted
from. A build SHALL fail rather than build with an input that does not match its pin. The
build inputs SHALL NOT be published or offered for download on their own. They reach
researchers only inside the packages.

#### Scenario: The pinned inputs are unavailable

- **WHEN** the pinned build-inputs commit cannot be fetched, because it is missing or access
  is refused
- **THEN** the build fails before building any package, naming the commit it could not fetch

#### Scenario: Looking for the corpus outside a package

- **WHEN** someone without access to the build-inputs repository looks for the converted
  corpus or the vector store on GitHub
- **THEN** they find neither, except inside a published package

#### Scenario: Two builds of the same tag

- **WHEN** the same tag is built twice
- **THEN** both builds ship the same corpus, vector store and embedding weights

#### Scenario: Which docs a release carries

- **WHEN** a maintainer asks which version of the OLCF docs a release ships
- **THEN** the pinned build inputs name the `olcf/olcf-user-docs` commit the corpus was
  converted from
