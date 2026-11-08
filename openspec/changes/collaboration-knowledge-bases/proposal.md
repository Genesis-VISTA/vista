## Why

Steps 4 and 5 of `PLAN.md` (`collaboration-skills`, `collaboration-projects`) share skills
and projects, but a project's knowledge bases travel only as slugs: an importer gets a
project naming papers it does not have. This is Step 6: knowledge bases are published and
imported, PDFs and built index together, and projects recurse into them. Indexing stays
local.

Three index faults go first, as a published index must be what search returns: deleting a
PDF left its passages in the index, per-PDF indexing never wrote the BM25 corpus, and the
MCP server never noticed a store replaced, rebuilt, reindexed or deleted under it.

## What Changes

- **Index fixes.** Deleting a PDF deletes its rows and rewrites the BM25 corpus (`409` while
  indexing). Every indexing run rewrites the corpus. Before each search the MCP server checks
  the knowledge base's folder and reopens its store, reloads its corpus, or drops it.
  ChromaDB clients are closed, not dropped. Deleting a knowledge base removes its folder.
- **Collaboration server:** an additive streamed upload (`POST /blobs/stream`, the raw
  body written to disk as it arrives, off the event loop).
- A knowledge base is an item of kind `knowledge-base`, named by its slug; a version's blob
  is a tar of its PDFs and a snapshot of its index (SQLite backup API, under the indexing
  lock); its manifest records publication metadata, embedding model and revision, and
  chromadb version.
- **BREAKING** `KnowledgeBaseTable` gains `origin_server`, `remote_id`, `remote_version`,
  `modified`, set by a real change of name or description and by adding or deleting a PDF.
- `POST /knowledge-bases/{slug}/publish`: refused while indexing; conflicts as for a skill.
- `GET /collaboration/knowledge-bases`, `POST /collaboration/knowledge-bases/{id}/import`:
  refuses another embedding model or an unreadable chromadb before downloading, verifies
  the download, and unpacks it into a new folder that `CURRENT` is switched to, so the
  store the MCP server reads is never written over. Slugs are handled like skill names.
- **BREAKING** Project manifest format 2: knowledge bases are references. A project publish
  publishes its changed knowledge bases after its skills; an import checks them before
  importing anything, then imports them (a taken slug gets `<slug>-<n>`) or keeps newer or
  changed copies. Format-1 versions are not imported.
- UI: Publish and a sharing line on each knowledge base, an **Import** dialog, knowledge
  bases in the project import list and summaries.
- No database migration: an older database must be deleted (`data/vista.db`) and recreated.

## Capabilities

### New Capabilities

- `knowledge-base-collaboration`: publishing, listing and importing knowledge bases.
- `knowledge-base-index`: what deleting a PDF, indexing and replacing a store do to the
  index `rag_search` reads.

### Modified Capabilities

- `project-collaboration` (from `collaboration-projects`, not yet archived): knowledge bases
  are references, published and imported with the project.

## Non-goals

- Remote indexing; incremental per-PDF uploads; resuming a failed upload.
- Deleting published versions, authentication, access control. VISTAGuard.
- Reading format-1 project manifests.
