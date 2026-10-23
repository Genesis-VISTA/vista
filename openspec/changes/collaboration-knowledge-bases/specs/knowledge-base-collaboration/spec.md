## Purpose

How a researcher shares a knowledge base through the collaboration server: publishing its
PDFs and built index, finding the server's knowledge bases, and importing one ready to search,
with every step explicit.

## ADDED Requirements

### Requirement: A knowledge base records where it stands with the collaboration server

Every knowledge base SHALL record the collaboration server, item and version it was published
as or imported from (none until then), and whether it has changed here since that version
(`modified`). Creating one, a change of its name or description, adding a PDF and deleting one
SHALL set `modified`; a save that changes nothing SHALL NOT; a publish or an import SHALL
clear it. Reading a knowledge base SHALL NOT set it.

#### Scenario: An edit after publishing

- **GIVEN** a knowledge base published as version 1
- **WHEN** the researcher saves its name unchanged
- **THEN** it is still version 1 and not modified
- **WHEN** the researcher renames it
- **THEN** it is modified, still recorded as version 1

### Requirement: Publishing a knowledge base

`POST /knowledge-bases/{slug}/publish` SHALL upload the knowledge base's PDFs and a snapshot of
its index as one archive, streamed to the collaboration server as one request body, as version 1 of a
new item of kind `knowledge-base` named by its slug, or as the next version on top of the
version it records. A knowledge base published and unchanged since SHALL NOT be published
again, and one that is indexing SHALL NOT be published.

#### Scenario: First publish

- **WHEN** the researcher publishes a knowledge base with two indexed PDFs
- **THEN** the server has a `knowledge-base` item whose version 1 archive holds `pdfs/` with
  both PDFs and `rag_db/` with a store that opens and holds their chunks
- **AND** the knowledge base records that item and version, and is not modified

#### Scenario: Nothing new

- **WHEN** the researcher publishes it again unchanged
- **THEN** nothing is uploaded and the answer is `unchanged`

#### Scenario: Indexing

- **WHEN** the researcher publishes a knowledge base that is indexing
- **THEN** the answer is `409` saying so, and nothing is sent

#### Scenario: Nothing indexed yet

- **GIVEN** a knowledge base created and given no PDF, so it has no index
- **WHEN** the researcher publishes it
- **THEN** it is published without an index, and an import of it is an empty knowledge base

### Requirement: A published version says what built its index

The index SHALL be snapshotted with SQLite's backup API while no indexing run or PDF deletion
can write the store. The manifest SHALL record the name, description, publication metadata,
the number and size of the PDFs, the archive's size, the embedding model and revision, and the
chromadb version, and SHALL NOT contain the row's id or paths.

#### Scenario: The manifest

- **WHEN** a knowledge base is published
- **THEN** its version's manifest names this VISTA's embedding model, revision and chromadb
  version, and not the knowledge base's id

### Requirement: A stale knowledge base publish lets the researcher choose

When the server's head has moved past the version the knowledge base records, a publish SHALL
be refused with `409` `version_conflict` and the head, offering: use the server's version
(an import replacing the copy here), publish on top of the head (`on_top_of`), or publish as a
new knowledge base (`as_new`). An item gone from the server SHALL be `409` `remote_missing`.

#### Scenario: Publish on top

- **GIVEN** a knowledge base published as version 1, changed here, whose item's head is 2
- **WHEN** the researcher publishes it
- **THEN** the answer is `409` `version_conflict` with head version 2
- **WHEN** they publish on top of version 2
- **THEN** it is published as version 3

### Requirement: The server's knowledge bases can be listed

`GET /collaboration/knowledge-bases` SHALL list the server's `knowledge-base` items, each with
its newest version's manifest, who published it, this installation's copy if any, and whether a
different knowledge base here has its slug. The Knowledge Bases page SHALL list them in an
import dialog showing what each would download and which embedding model indexed it, and SHALL
disable its **Import** button, showing the reason, when no collaboration server answers.

#### Scenario: A slug taken here

- **GIVEN** a knowledge base here with the slug `salt-papers`, not from the server
- **THEN** the server's `salt-papers` is listed with `name_taken`, and the dialog offers
  "Import as…"

### Requirement: Importing a knowledge base

`POST /collaboration/knowledge-bases/{id}/import` SHALL import a version (the newest by default)
with its PDFs and index, searchable at once without indexing, creating a knowledge base with an
id of its own or updating the copy from the same item in place, keeping its id and slug.

#### Scenario: A new knowledge base

- **WHEN** the researcher imports a knowledge base published elsewhere
- **THEN** it is created with the version's name, description and publications, its PDFs can
  be opened, its store holds the version's chunks, and it records the item and version, not
  modified

### Requirement: An import that needs a choice is refused before downloading

An import SHALL refuse, before downloading anything, a copy with changes made here (`409`
`modified`, until confirmed), a copy that is indexing (`409`), and a slug that is another
knowledge base's (`409` `name_taken`) or not a valid slug (`409` `name_invalid` for the
item's, `400` for one typed).

#### Scenario: A taken slug

- **GIVEN** a different knowledge base here with the slug
- **WHEN** the researcher imports it
- **THEN** the answer is `409` `name_taken`
- **AND** importing it as `salt-papers-bea` succeeds, leaving the one here untouched

#### Scenario: Changes here

- **GIVEN** a knowledge base imported as version 1 and renamed here, whose item's head is 2
- **WHEN** the researcher imports version 2
- **THEN** the answer is `409` `modified`, and with `replace_modified` it becomes version 2

### Requirement: One publish or import of a knowledge base at a time

A publish or import of a knowledge base SHALL hold a lock on its slug from its checks to its
commit, and an import until the store it replaced is removed. An import that finds the copy
here imported or deleted by another request while it waited SHALL be refused (`409`).
Creating a knowledge base whose slug is being imported, and deleting one being published or
imported, SHALL be refused (`409`). Creating one SHALL remove a `CURRENT` file and stores a
failed deletion left in its folder. A publish SHALL be refused (`507`) before packing when its
snapshot and archive would not fit on the disk.

#### Scenario: Two imports at once

- **WHEN** the same knowledge base is imported twice at the same time
- **THEN** one imports it and the other finds it imported or is refused, and `CURRENT`, the
  row and the only `store-*` folder name the same store

### Requirement: An import refuses an index this VISTA cannot search

Before downloading, an import SHALL refuse (`409`, saying why) a version whose index was built
with another embedding model or revision than this VISTA searches with, or by a chromadb of
another major version or newer than this one's. It SHALL refuse a version whose archive and
unpacked copy would not fit in the free space beside the knowledge bases.

#### Scenario: Another embedding model

- **WHEN** the researcher imports a knowledge base indexed with another model
- **THEN** the answer is `409` naming the model, nothing is downloaded, and nothing is created

### Requirement: An imported archive holds only PDFs and an index

The download SHALL be refused past the manifest's archive size and when its SHA-256, computed
as it is written, is not the version's blob reference. Unpacking SHALL take only regular files
under `pdfs/` (PDF files) and `rag_db/`, refusing absolute paths, `..`, backslashes, a Windows
drive, any member that would resolve outside the import, duplicates, links and special files, and an archive with no `rag_db/chroma.sqlite3` unless the manifest says it has
no index.

#### Scenario: A path outside

- **WHEN** an archive holds `pdfs/../../outside.pdf`
- **THEN** the import is refused and nothing is written outside the knowledge base

### Requirement: An update never writes over the store being searched

An import SHALL unpack into a new folder of the knowledge base, then point the knowledge base's
`CURRENT` file at it and the row at its paths, so the store the MCP server has open is never
changed under it. The store it replaced SHALL be removed when nothing here has it open, and
otherwise on a later import or the knowledge base's deletion.

#### Scenario: An update in place

- **GIVEN** a knowledge base imported as version 1
- **WHEN** the researcher imports version 2
- **THEN** it keeps its id and slug, `CURRENT` names a new folder holding version 2's store,
  and version 1's folder is removed
