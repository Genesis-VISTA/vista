## Context

A knowledge base here is a row (`KnowledgeBaseTable`: slug, name, description, the
publications' metadata as JSON, absolute `pdfs_dir` and `rag_db_path`) and a folder,
`<knowledge_bases_dir>/<slug>/{pdfs,rag_db}`. `rag_db` is a ChromaDB 1.x persistent store
(`chroma.sqlite3`, in rollback-journal mode, plus one folder of HNSW files per segment) and,
for hybrid retrieval, `bm25_corpus.json`. The backend indexes PDFs one at a time
(`utils/indexer.py` over `build_rag.TextRAG.index_single_pdf`), serialised per store by an
asyncio lock. The MCP server (`rag_mcp.py`) is another process with no database: it finds
stores by scanning `knowledge_bases_dir`, and opened each once, caching its client and BM25
index. Skills and projects are already shared (`collaboration-skills`,
`collaboration-projects`).

## Decisions

### D1: Fix the index first

- **Deleting a PDF** (`DELETE /knowledge-bases/{slug}/publications/{file}`) deletes the
  chunks and citation whose `source` is the file name, then rewrites the BM25 corpus, under
  the store's indexing lock (`indexer.delete_source`), and only then removes the file and the
  entry. If the index cannot be changed, nothing is, and the delete can be retried. Refused
  (`409`) while the knowledge base is indexing, rather than waiting minutes in a request.
- **BM25 on every run.** `index_publications` ends each run with
  `TextRAG.write_bm25_corpus()`. The writer (`build_rag.write_bm25_corpus`) is a module
  function that needs nothing from the MCP server's package, which the backend cannot import:
  it writes format v1 without the optional `tokens`, so the MCP server tokenizes on load and
  the tokenizer exists once. It replaces the file in one step (temp + `os.replace`); an
  empty collection removes it.
- **The MCP server follows the folder.** Before a search it runs `_refresh_kb(slug)`: it
  reads the store path (`_store_path`, see D5) and `stat`s the BM25 corpus, and compares
  both with what the handle opened. A new path or a changed corpus reopens the store and
  reloads the corpus; a store gone drops the KB. The corpus is the signal because every
  backend path that writes a store rewrites it afterwards in one step (an indexing run, a
  PDF's deletion), so its device, inode, size and mtime change; a store deleted and built
  again has another corpus, or none. `chroma.sqlite3` is not a usable signal: opening a
  store with no client open on it in that process writes the file, and the backend opens
  each store to read its citations whenever a knowledge base page lists them (round 1
  measured this; watching the database made the MCP server reopen stores on reads). A store
  indexed by the backend is now seen by the next search; it was not before. The whole
  search runs in a thread (`asyncio.to_thread`), one at a time under a lock, so a reopen or
  a corpus load no longer blocks the MCP server's event loop.
- **The reconciler's citation cache** is keyed by `chroma.sqlite3`'s mtime as the read left
  it, not as it was before: since its client is closed, the next open would otherwise miss
  the cache every time.
- **Clients are closed.** ChromaDB keeps one client per path per process, so one left open
  makes a store rebuilt at the same path read through the stale client, and on Windows
  keeps its folder from being removed. `invalidate_rag_cache`, the reconciler's citation
  read and the MCP server's handles now close theirs.
- **Deleting a knowledge base** removes `<knowledge_bases_dir>/<slug>` as well as its two
  paths, so stores an import left behind (D5) go too.

### D2: A streamed upload on the collaboration server

`POST /blobs` is one multipart request, spooled whole by Starlette before the route runs:
fine for a skill, not for gigabytes. The additive `POST /blobs/stream` takes the blob as the
raw request body, which Starlette does not spool, and writes it to the store's temporary file
as it arrives, in 1 MiB writes off the event loop, hashing on the way
(`blobs.store_stream`, which `POST /blobs` now uses too). `max_blob_size` caps it; a body
that fails or ends early leaves nothing. `API_VERSION` stays 1: nothing an older backend
reads changed.

The backend's `CollaborationClient.upload_large_blob` sends the file as an async iterator
(chunked transfer), reading 8 MiB at a time in a thread, and refuses a server reference other
than its own digest. `download_blob` checks the digest the same way as it writes
(`blob_ref=`), so neither importer reads its download twice.

Round 1 replaced a four-route resumable upload (open, `PUT ?offset=`, complete, abandon)
with this: the client never resumed, so the protocol bought nothing yet. It can come back
with a deployment that needs it (a proxy limiting each request's body) and a client that
resumes.

### D3: What a version is

An item of kind `knowledge-base` named by the publisher's slug. Its blob is an uncompressed
tar (PDFs and float vectors hardly compress): every PDF under `pdfs_dir` (recursively, links
left out) as `pdfs/…`, and the store snapshot as `rag_db/…`. Ownership, mtimes and modes are
normalised. The manifest (`KnowledgeBaseManifest`, `format: 1`) holds slug, name,
description, the publications (entries still `queued` or `indexing` shipped as `unindexed`),
build status and time, PDF count and bytes, the archive's size, and what built the index:
`embedding_model` and `embedding_revision` (new `build_rag.TEXT_MODEL` and
`TEXT_MODEL_REVISION`, which `TextRAG` defaults to and `test_embedding_model.py` keeps equal
to the MCP server's) and the `chromadb` version. Nothing of the row goes that is not named.

A knowledge base nothing has been indexed into has no store yet. It is published without
`rag_db/` (`has_index: false`); an importer skips the compatibility check and starts it with
an empty `rag_db/`. A project that searches a newly created knowledge base can be published.

**The snapshot** (`indexer.snapshot_store`) holds the store's indexing lock, so no run or PDF
deletion writes it meanwhile, copies every file but the database, then copies the database
with SQLite's backup API, which reads a consistent copy even while a client has it open.
Unflushed vectors are in the database's log, which ChromaDB replays on open, so files and
database taken under the lock agree. A publish is refused while `index_progress` is set.

### D4: Publishing

Like a skill (`publish_knowledge_base`): unchanged when linked and not modified (no upload),
else check the free space for a snapshot and an archive of the PDFs and store (`507`),
snapshot, pack under `<knowledge_bases_dir>/.incoming/<id>/`, upload, and create the item
or publish on `remote_version` / `on_top_of`, with `version_conflict` and `remote_missing`
as `KnowledgeBaseConflict`. `modified` is cleared only if `kb_content` (name, description,
PDF names and sizes) is unchanged after the server answered. Not `updated_at`, which a GET
moves: the reconciler records what it finds on disk and in the index.

### D5: Importing never writes over a store

`<knowledge_bases_dir>/<slug>/CURRENT`, when present, names the subfolder that holds the
knowledge base's `pdfs/` and `rag_db/`. The MCP server reads it (`_store_path`); a `CURRENT`
naming no store is no store, never a fallback to an older one. The backend ignores it and
uses the row's paths. An import:

1. checks the item and version, the copy here (unchanged → `unchanged`; `modified` → `409`
   until confirmed; indexing → `409`) or the slug (`is_valid_slug`: a typed one `400`, the
   item's `409 name_invalid`; taken → `409 name_taken`);
2. refuses an index it cannot search (`incompatibility`): another embedding model or
   revision; a chromadb of another major version, or newer than this one (chromadb migrates
   an older store forward on open and cannot read a newer one); then checks free space for
   the archive and its unpacked copy;
3. downloads into `.incoming/` (capped at the manifest's `archive_size`, the digest checked
   as it is written), unpacks regular files under `pdfs/` (PDFs only) and `rag_db/` only,
   refusing what a skill upload refuses (`skill_import.clean_relpath`: absolute paths, `..`,
   empty parts, backslashes and `:`, a Windows drive), any member whose joined path does not
   resolve inside the import, duplicate and special entries and, unless the manifest says it
   has none, an archive without `rag_db/chroma.sqlite3`, and moves the result to
   `<slug>/store-<id>/`;
4. re-reads the copy (a change meanwhile, by `kb_content`, or indexing → `modified`, the new
   folder removed) or the slug, writes `CURRENT` in one step, and commits the row: a new
   `uuid4` (seeded knowledge bases share fixed ids) or the copy in place, keeping id and
   slug. A failed commit restores `CURRENT` and removes the new folder;
5. for an update, closes and forgets the backend's clients on the old store, and removes it,
   any other `store-*` folder and the canonical `pdfs/` and `rag_db/`, best effort. A new
   import removes nothing: what its folder held, no row described. One the MCP server still has open on Windows stays until a
   later import or the deletion of the knowledge base.

The MCP server's next search sees the new path (D1) and opens it. Nothing tells it; nothing
needs to.

**One at a time per slug.** A publish and an import of a knowledge base hold its slug's lock
(`knowledge_base.kb_lock`) from their checks through the commit, and an import through
removing what it replaced. Two imports interleaved could otherwise leave `CURRENT` and the
row naming different stores, each removing the other's. The import decides the slug, takes
the lock, then reads the copy here again and refuses (`409`, import it again) if another
request imported or deleted it meanwhile. Creating a knowledge base whose slug is being
imported is refused, and so is deleting one that is being published or imported (`409`), and creating one removes a stale `CURRENT` and `store-*` that a failed
deletion left, so the MCP server cannot serve the dead store. What publishes and imports
leave in `.incoming/` is removed at backend startup.

### D6: Projects recurse into knowledge bases

`ProjectManifest` format 2: `knowledge_bases` are `KnowledgeBaseRef {slug, remote_id,
version}`, the slug a label and the starting point for a new one's slug. Format 1 is no
longer imported: no installation has published under it outside this branch.

**Publishing.** Before anything is sent, `_check_knowledge_bases` refuses a project
searching a knowledge base that is indexing (`409`), or one not here that the version built
on did not reference (`400`, remove it first). One the base referenced is carried as that
reference, like a bundled skill this VISTA lacks. Then skills, then knowledge bases
(`publish_knowledge_base`; a conflict is the project's `knowledge_base_conflict`, with
`knowledge_base` and `knowledge_base_code`), then the project. "Unchanged" compares skill
and knowledge base references (`_references`).

**Importing.** `_plan_knowledge_bases` runs before any skill is imported: a reference with no
copy here is `import`, a copy at that version and unchanged `unchanged`, an older unchanged
copy `update`, anything else `kept`; every version to be downloaded is fetched and checked
for compatibility, so an unsearchable one refuses the whole import (`409`, "<project> was not
imported: …") with nothing imported. The plan also refuses a copy it would update that is
indexing (`409`), and checks the free space for everything it would download at once
(`507`). Then skills, then knowledge bases: a new one takes the
reference's slug or the first free `<slug>-<n>`, picked here (a dialog per knowledge base
would make a project import a questionnaire). The result's `knowledge_bases` lists each
outcome. A kept copy does not make the project modified, as for skills.

### D7: UI

`lib/kb-sharing.ts` mirrors `lib/skill-sharing.ts`. The knowledge base page shows the sharing
line and Publish (first publish confirmed, off while indexing or without a server, conflicts
in `PublishConflictModal` with noun "knowledge base"). **Import** opens
`KnowledgeBaseImportModal`: size to download, embedding model, rename prompt with the slug
rule (`slugError`), replace confirmation. The project import list counts and tags knowledge
bases; `importSummary` and `publishedFirst` name them; `knowledge_base_conflict` links to
`/knowledge-bases`. The publication delete proxy passes the backend's reason through.

## Risks

- A large knowledge base takes as long to publish or import as its bytes take to move. The
  UI's proxies use Node's `fetch`, which gives up waiting for headers after 5 minutes while
  the backend carries on. Fine on loopback, short of a progress API.
- The snapshot copies the HNSW files while the MCP server may have the store open. It only
  reads, so the lock covers every writer; if ChromaDB ever wrote on read, a snapshot could
  pair a newer index file with an older database.
- A store written by something other than VISTA's indexer, without rewriting its corpus, is
  not noticed by a running MCP server until it restarts.
- The corpus carries no tokens, so the MCP server tokenizes a large knowledge base's whole
  corpus each time it reopens it after an indexing run: CPU at search time, in a thread.
- Publishing the seeded molten-salt project uploads its ~300 MB corpus, and an importer with
  its own seeded copy downloads another. Seeded knowledge bases are not referenced like
  bundled skills (`bundled:<name>`), because they do not ship identically with every install:
  - Bundled skills are files in the repository (`db/skills/`), so every installation has
    them. The seeded knowledge bases come from vista-data: `molten-salt-papers` only when the
    science projects are seeded (a vista-data token, or a package built with them), and
    `ai-safety` only when vista-data is reachable. A checkout with neither has no seeded
    knowledge base, so a reference by name would import a project that silently searches
    nothing.
  - What one holds is not fixed either: a token install downloads the PDFs vista-data has
    that day and indexes them itself, and a researcher may add or delete PDFs in it.
  - A later step could avoid the duplicate download, not the upload: an importer whose own
    seeded copy (same fixed id, unmodified, same PDF names and sizes as the manifest) matches
    could use it instead. Not done here.
- An imported PDF is the publisher's file, opened by the researcher's PDF viewer. Importing
  is the researcher's trust decision, as for a skill.
- An older database must be deleted and recreated; a collaboration server's format-1 project
  versions are no longer importable.
