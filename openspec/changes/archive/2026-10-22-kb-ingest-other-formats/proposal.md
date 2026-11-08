## Why

Knowledge bases ingest only PDFs, so a researcher's tabular data and notes can't be searched
alongside their papers. When a search does point at data, the agent has no way to get the
file into its sandbox to compute on it. This was raised in hackathon feedback (issue #88).

## What Changes

- **Accepted formats:** knowledge bases accept CSV, TSV, TXT and MD alongside PDF, indexed
  into the same Chroma store. One list of formats governs upload, listing, download and
  indexing.
- **Tables:** CSV and TSV are indexed as a summary plus row windows. The summary holds the
  columns, row count, types, sample rows and an LLM description. Row windows are ~50 rows
  and repeat the header, capped at ~200 windows. The summary notes when the windows were
  truncated.
- **Descriptions:** every non-PDF file gets an LLM description, stored as its citation row
  (a dataset or a note). It is skipped without a model credential.
- **Ids:** Chroma ids use the full file name for every format, so `foo.pdf` and `foo.csv`
  no longer clash. Skipping and re-indexing key on `source`, so old, new and mixed stores
  all behave correctly with no migration.
- **Delete:** deleting a file also removes its chunks and citation row from the store.
- **Search hits:** each hit names its format and location (`salts.csv, rows 51-100 (CSV)`).
  Non-PDF hits say the full file can be fetched.
- **New agent tool:** `fetch_kb_file(kb_slug, filename)` copies a file from one of the
  project's knowledge bases into `/mnt/data/work/kb/<slug>/`. The agent then works on the
  complete file, whatever was indexed.
- **Explorer:** non-PDF files show a format badge, the description and, for tables, the row
  count.
- **Release inputs:** the AI-safety store in vista-build-inputs is regenerated with the new
  ids, and the pin in `.github/build-inputs.env` is bumped. Older pins stay resolvable.
- **Hands-on test:** a synthetic CSV knowledge base with known values. The agent finds
  specific values, fetches the files and plots them.

## Non-goals

- XLSX and DOCX, which need new dependencies. They are left for later changes.
- A CSV preview in the UI.
- An automated end-to-end CI test over the fixture, which is for the hands-on test only.
- Mounting knowledge bases into the sandbox.
- Changes to the project datasets (uploads) page.
- VISTAGuard / PALISADE.

## Capabilities

### New Capabilities
- `knowledge-bases`: accepted formats, how each is chunked and described, how a file's
  indexed content is identified and replaced, deletion, what search hits say about their
  source, and fetching a file into the sandbox.

### Modified Capabilities
None. The `release-builds` requirement that inputs are pinned by commit is unchanged; only
the pin moves.

## Impact

- **Indexing:** `build_rag.py` and `backend/src/vista_backend/utils/indexer.py`.
- **Backend:** `backend/src/vista_backend/api/knowledge_bases.py` and
  `db/schemas.py` (`Publication.kind`, `row_count`).
- **Agent:** `backend/src/vista_backend/agents/agents.py` (`fetch_kb_file`).
- **Search:** `mcp_servers/vista_mcp_server/src/vista_mcp_server/rag_mcp.py`.
- **UI:** `ui/app/knowledge-bases/explorer.tsx` and `ui/lib/types.ts`.
- **Release:** Genesis-VISTA/vista-build-inputs (a new commit) and `.github/build-inputs.env`.
- **Dependencies:** none new.
