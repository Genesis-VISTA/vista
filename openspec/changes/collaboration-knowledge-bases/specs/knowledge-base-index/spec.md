## Purpose

What changing a knowledge base does to the index `rag_search` reads: the ChromaDB store and
BM25 corpus beside each knowledge base, written by the backend and read by the MCP server.

## ADDED Requirements

### Requirement: Deleting a PDF deletes it from the index

Deleting a publication SHALL delete its text chunks and citation from the knowledge base's
store and rewrite its BM25 corpus before the PDF and its entry are removed. It SHALL be refused
(`409`) while the knowledge base is indexing. If the store cannot be changed, nothing SHALL be.

#### Scenario: A deleted paper is not found

- **GIVEN** a knowledge base with two indexed PDFs
- **WHEN** the researcher deletes one
- **THEN** the store holds only the other's chunks, the BM25 corpus only its text, and the
  knowledge base is modified

### Requirement: Indexing writes the BM25 corpus

Every indexing run SHALL end by rewriting the store's BM25 corpus from every chunk in it, in one
step, without needing the MCP server's package. An empty store SHALL have no corpus.

#### Scenario: A PDF uploaded in the UI

- **WHEN** a PDF uploaded to a knowledge base has been indexed
- **THEN** `bm25_corpus.json` beside its store holds its chunks, and hybrid `rag_search` uses it

### Requirement: The MCP server follows a knowledge base's folder

Before searching a knowledge base, the MCP server SHALL check its folder and reopen its store
when the store's path or its BM25 corpus has changed (a new store named by `CURRENT`, one
rebuilt, or one written by the backend, which rewrites the corpus after every write), and drop
the knowledge base when its folder no longer holds a store. A `CURRENT` file naming no store
SHALL mean no store. Merely opening or reading a store elsewhere SHALL NOT make it reopen.

#### Scenario: A replaced store

- **GIVEN** a knowledge base the MCP server has searched
- **WHEN** an import points its `CURRENT` at a new store
- **THEN** the next search reads the new store, and the old client is closed

#### Scenario: A store indexed meanwhile

- **WHEN** the backend adds chunks to a store the MCP server has open
- **THEN** the next search sees them

#### Scenario: A store only read

- **WHEN** the backend opens a store to read its citations, as listing knowledge bases does
- **THEN** the MCP server does not reopen it, and a second listing does not open it again

#### Scenario: A deleted knowledge base

- **WHEN** a knowledge base's folder is removed
- **THEN** the next search for it answers that the slug is unknown

### Requirement: ChromaDB clients are closed

The backend and the MCP server SHALL close a ChromaDB client they no longer use, rather than
drop it, so a store rebuilt at the same path is read afresh and, on Windows, a replaced
store's folder can be removed.

#### Scenario: A store rebuilt at the same path

- **GIVEN** a knowledge base deleted and created again with the same slug
- **WHEN** it is indexed and searched
- **THEN** the search reads the new store

### Requirement: Deleting a knowledge base removes its folder

Deleting a knowledge base SHALL remove its folder under `knowledge_bases_dir`, with any stores
an import left behind, as well as its PDFs and store.

#### Scenario: Leftovers of an import

- **GIVEN** a knowledge base whose folder holds a `CURRENT` file and an old store folder
- **WHEN** the researcher deletes it
- **THEN** its folder is gone
