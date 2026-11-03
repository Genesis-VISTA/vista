## Purpose

Defines how the OLCF user documentation becomes VISTA's bundled knowledge-base corpus: what
it contains, how its files are named and headed so every passage can be cited with its
docs.olcf.ornl.gov URL and status, and when the conversion runs.

## ADDED Requirements

### Requirement: The corpus is the whole OLCF docs repository at a recorded commit

The `olcf-docs` corpus SHALL be generated from every documentation page in the
`olcf/olcf-user-docs` repository at a single commit, including pages about retired,
preproduction and decommissioned systems. The commit SHALL be recorded beside the corpus.
Images and other non-text files SHALL NOT be part of the corpus.

#### Scenario: Converting the repository

- **WHEN** the converter is run against a checkout of `olcf/olcf-user-docs` at a commit
- **THEN** every documentation page in the checkout yields at least one corpus file, and the
  corpus records that commit

#### Scenario: Legacy systems are kept

- **WHEN** the repository documents a decommissioned system such as Summit
- **THEN** that system's pages are in the corpus

### Requirement: One file per section, named from its path

Each page SHALL be split at its top-level sections into one Markdown file per section, plus
one for any content before the first section. A section longer than 2,000 words SHALL be
split again at its subsections. Each file's name SHALL be derived from the page's path in the
repository and the section's anchor, so that names are unique across the corpus and the
docs.olcf.ornl.gov URL can be derived from the name alone.

#### Scenario: Two pages with the same file name

- **WHEN** the repository holds `systems/index.rst` and `data/index.rst`
- **THEN** their corpus files have different names

#### Scenario: A section file's name

- **WHEN** the Frontier user guide's "Running Jobs" section is converted
- **THEN** its file is named from `systems/frontier_user_guide` and the section's anchor,
  for example `systems__frontier_user_guide__running-jobs.md`

#### Scenario: A long section

- **WHEN** a top-level section holds more than 2,000 words
- **THEN** it is written as one file per subsection, each no longer than its own subsection

### Requirement: Each file starts with its title, URL and status

Each corpus file SHALL start with a header that gives the page title and section title, the
section's URL on docs.olcf.ornl.gov (with the section's anchor), and every notice that
appears at the top of its page before the first section, such as an archive or
decommission notice. The header SHALL be written by the converter, not by a language model.

#### Scenario: A current system's section

- **WHEN** a section of the Frontier user guide is converted
- **THEN** its file starts with "Frontier User Guide", the section's title and its
  `https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#<anchor>` URL

#### Scenario: A decommissioned system's section

- **WHEN** a section of a page whose top carries a notice that the system was
  decommissioned is converted
- **THEN** that notice appears in the header of every file made from the page

#### Scenario: The URL resolves

- **WHEN** a URL written into a header is opened on docs.olcf.ornl.gov at the corpus's
  commit
- **THEN** it opens that page at that section

### Requirement: Conversion runs only when the corpus is refreshed

The conversion SHALL run only when a maintainer refreshes the corpus, and SHALL NOT run
during a package build or on a researcher's machine. The corpus SHALL be indexed by the
same indexer as any other Markdown knowledge base, with no indexing behaviour specific to
it.

#### Scenario: Building a package

- **WHEN** a package is built from the pinned build inputs
- **THEN** no OLCF page is fetched or converted, and the corpus is taken as it was committed

#### Scenario: Indexing the corpus

- **WHEN** the converted corpus is indexed
- **THEN** each file gets the same chunking and description as any Markdown file in a
  researcher's own knowledge base
