## 1. Index fixes (prerequisites)

- [x] 1.1 `build_rag.py`: `TEXT_MODEL`, `TEXT_MODEL_REVISION` (TextRAG's defaults); `write_bm25_corpus` (format v1 without tokens, replaced in one step, no MCP import); `TextRAG.write_bm25_corpus`
- [x] 1.2 `utils/indexer.py`: each `index_publications` run rewrites the corpus; `delete_source` and `snapshot_store` under the store's lock; `store_format`; `invalidate_rag_cache` closes the client
- [x] 1.3 `api/knowledge_bases.py`: deleting a PDF deletes its rows first (`409` while indexing); the reconciler closes its client; deleting a knowledge base removes `<knowledge_bases_dir>/<slug>`
- [x] 1.4 `rag_mcp.py`: `_store_path` (`CURRENT`, `rag_db/`, legacy), `_refresh_kb` before each search (reopen, reload the corpus, drop), handles keep and close their client
- [x] 1.5 Tests: `mcp_servers/vista_mcp_server/tests/test_rag_kb_discovery.py` (imported store, bad `CURRENT`, replaced, deleted, rebuilt at the same path, written elsewhere, corpus rewritten); `tests/test_embedding_model.py` reads the constants; `backend/tests/test_indexer_paths.py` (corpus written once per run)

## 2. Collaboration server

- [x] 2.1 `POST /blobs/stream` (`blobs.store_stream`): the raw body written as it arrives, off the event loop; `max_blob_size` (replaced the `/uploads` protocol in round 1)
- [x] 2.2 Tests: `collaboration_server/tests/test_blob_stream.py`; `README.md` API table

## 3. Backend

- [x] 3.1 `KnowledgeBaseSharing` on `KnowledgeBaseTable` and `KnowledgeBasePublic`; `modified` set by `update_kb` (a real change), adding and deleting a PDF; `kb_content`, `kb_folder`, `CURRENT_STORE_FILE`
- [x] 3.2 `CollaborationClient.upload_large_blob` (streamed body, digest check)
- [x] 3.3 `services/knowledge_base_collaboration.py`: manifest, `incompatibility`, pack/unpack, `publish_knowledge_base`, `list_remote_knowledge_bases`, `import_knowledge_base` (new folder, `CURRENT`, old stores removed)
- [x] 3.4 Routes `POST /knowledge-bases/{slug}/publish`, `GET /collaboration/knowledge-bases`, `POST /collaboration/knowledge-bases/{id}/import`
- [x] 3.5 `project_collaboration.py`: format 2 `KnowledgeBaseRef`; publish checks, publishes and carries knowledge bases; import plans and checks them before skills, imports, updates or keeps them; `knowledge_base_conflict`
- [x] 3.6 Tests: `backend/tests/test_knowledge_base_collaboration.py` (with `tests/harness/knowledge_bases.py`), `backend/tests/test_project_collaboration.py`

## 4. UI

- [x] 4.1 `KnowledgeBase` sharing fields; proxies for publish, the listing and import; the publication delete proxy passes the reason through
- [x] 4.2 `lib/kb-sharing.ts`; `lib/project-sharing.ts` (`KnowledgeBaseRef`, `knowledgeBasesPublished`, `knowledgeBases` outcomes, `publishedFirst`); `knowledge_base_conflict` in `SharingRefusal`
- [x] 4.3 Knowledge base page: sharing line, Publish (confirmation, off while indexing, conflict choices), **Import** with `KnowledgeBaseImportModal`
- [x] 4.4 Projects page and import list: knowledge bases in the summary, counts and tags; the Knowledge Bases page link
- [x] 4.5 Tests: `ui/tests/kb-sharing.test.tsx`, `ui/tests/project-sharing.test.tsx`; hermetic fixture's sharing fields

## 4b. Review round 1

- [x] 4b.1 MCP reopens on a new store path or a rewritten corpus, not on `chroma.sqlite3` (which a read writes); the reconciler's cache keyed by the mtime its read left; searches run in a thread under a lock
- [x] 4b.2 An empty knowledge base is published without an index (`has_index`), and imported as an empty one
- [x] 4b.3 Per-slug lock (`kb_lock`) over publish and import, the copy re-read under it; `create_kb` refuses a slug being imported and removes a stale `CURRENT` and `store-*`; `.incoming/` cleared at startup; `_remove_stores` also removes the canonical `pdfs/`, `rag_db/`
- [x] 4b.4 Project import plans check copies being updated are idle and the total download fits
- [x] 4b.5 Snapshot opens the database with `Path.as_uri()`
- [x] 4b.6 `POST /blobs/stream` replaces the `/uploads` protocol; `download_blob(blob_ref=)` checks the digest while writing, and both importers stop hashing again
- [x] 4b.7 Nits: `required_url`, `SLUG_PATTERN` and `CURRENT` helpers each in one place, `clear_progress` public, `importAction` and `formatSize` shared, the manifest's PDF size shown, `_build_bm25_corpus` removed
- [x] 4b.8 Tests: empty knowledge base, two concurrent imports, project import idle and disk checks, a `#`/`%`/`?` path, listing without rewriting, re-creating after a failed deletion, a read store not reopened, the stream upload and the download digest

## 4c. Review round 2

- [x] 4c.1 Deleting a knowledge base being published or imported is refused (`409`)
- [x] 4c.2 A publish checks free space for its snapshot and archive before packing (`507`)
- [x] 4c.3 Nits: `_import` and `_publish` take keyword arguments; one `_search` takes the search lock; `indexer.clear_progress` is the only name; the import dialog calls `importAction`

## 4d. Review round 3

- [x] 4d.1 The unpacker uses the skill upload's path rule (`clean_relpath`, which refuses `:`) and checks each joined path resolves inside the import
- [x] 4d.2 Only an update removes the stores it replaced; a new import leaves untracked data in its folder

## 5. Docs and checks

- [x] 5.1 `docs/project-onboarding.md`, `collaboration_server/README.md`, `AGENTS.md`
- [x] 5.2 `./scripts/ci-local.sh backend collab mcp ui docs` and `openspec validate --all`
- [ ] 5.3 Archive `collaboration-projects` before this change: its `project-collaboration` spec is what this change's delta modifies
