## Context

See proposal.md for the motivation. The current state that shapes the approach:

- **`build_rag.TextRAG` does the indexing**, and `utils/indexer.py` wraps it for the backend.
  It indexes one file at a time with `index_single_pdf`. That path extracts the citation
  (one LLM call), then the text: PyMuPDF pages, ~512-word chunks with metadata
  `{source, page, chunk_id}`. The chunks go into the `text_chunks` collection and the
  citation into `citations`.
- **Ids come from the file stem.** Chunks are `<stem>_text_<j>` and the citation row is
  `<stem>_citation`. The "already indexed" check is `citations.get(ids=[<stem>_citation])`.
  Re-indexing relies on the upsert hitting the same ids.
- **Nothing outside indexing reads by id.** Citation lookup (`rag_mcp._get_citation_for_source`,
  `build_rag.get_citation_for_source`), the backend's reconciler (a full metadata scan), the
  package build's store check (`build_local_package.sh:check_store_matches_corpus`) and
  seeding (`.count()`) all go by `source` or by count. The BM25 corpus file
  (`bm25_corpus.json`) is built from the live collection's own ids.
- **Deleting a publication** (`api/knowledge_bases.py:delete_publication`) unlinks the file
  and leaves its Chroma rows. The reconciler then shows those rows as an "indexed only"
  stub with `has_pdf=False`.
- **Each `ProjectAgent` sandbox** mounts the project volume read-write at `/mnt` and skills
  read-only at `/mnt/skills`. Mounts are fixed when the sandbox spawns. `fetch_repository`
  (`agents/agents.py`) is the precedent for an agent tool that writes into
  `/mnt/data/work/`.
- **The project datasets page** manages `/mnt/data/uploads`. The sandbox can already see
  those files, but search cannot. A knowledge-base file is the reverse: searchable, shared
  across projects, and outside the sandbox until fetched.
- **Release builds** ship the AI-safety store from `Genesis-VISTA/vista-build-inputs`,
  pinned by full SHA in `.github/build-inputs.env` and copied into the package without
  re-indexing. The update procedure is in `docs/releasing.md` § "The build inputs".

## Goals / Non-Goals

**Goals:**
- One extractor per format behind a single per-file entry point, so adding XLSX or DOCX
  later means adding one function.
- Identity, skip and replace keyed on `source`, so the store's correctness never depends on
  how ids are spelled.
- No new dependencies. CSV, TSV, TXT and MD are read with the standard library.

**Non-Goals:**
- Making the BM25 corpus fresh after every single-file index. That gap exists today and is
  only touched where this change deletes rows (see Decisions).
- Type-aware search, such as numeric range queries over CSV columns. The agent does that in
  the sandbox after fetching.

## Decisions

### One per-file entry point, extractors chosen by suffix
`index_single_pdf` becomes `index_single_file(path, ...)`, and the indexer wrapper calls it
for every format. A suffix → extractor table maps `.pdf`, `.csv`, `.tsv`, `.txt` and `.md`.
Each extractor returns the existing chunk shape `{text, metadata}`, with `metadata.kind` set
to `pdf | csv | tsv | text | markdown`. The location field is `page` for PDFs, `rows`
(`"51-100"` or `"summary"`) for tables, and `chunk_id` for text. The same table backs the
upload allow-list and the on-disk scan, so there is one list of formats. The table lives in
`build_rag.py`, and the backend imports it the way it already imports `TextRAG`.

*Alternative:* separate pipelines per format. Rejected: progress, skipping and LLM gating
would each be duplicated.

### Ids use the full file name; identity uses `source`
- **Ids:** chunks become `<filename>_text_<j>` and citation rows `<filename>_citation`, for
  every format.
- **Skip check:** `text_chunks.get(where={"source": filename}, limit=1)`. This works for
  files with no citation row, and on stores with old-style ids.
- **Re-index:** first deletes `where={"source": filename}` from both collections, then
  writes.
- **Delete endpoint:** uses the same delete-by-source helper.

Old, new and mixed stores therefore behave identically, so no migration is needed. A store
built by an earlier version keeps its old ids until a file is re-indexed.

*Alternatives:*
- Full-name ids only for new formats. Rejected (user decision): two id schemes.
- Rebuilding stores on upgrade. Rejected: it re-embeds every KB and repeats every citation
  call.

### CSV/TSV: deterministic summary + LLM description + capped row windows
- **Parsing:** read with `encoding="utf-8-sig"` (strips a BOM). `csv.Sniffer` picks the
  delimiter from a sample, falling back to `,` for `.csv` and tab for `.tsv`.
- **Type inference:** per column over the parsed rows: int, float, otherwise text, ignoring
  blanks.
- **Summary passage:** name, columns with types, row count, the first 5 rows, the
  description if any, and the truncation note when the cap applies.
- **Row windows:** 50 rows, header-prefixed, up to 200 windows. These are module
  constants, not settings.
- **Size:** rows past the cap are still read, but only to count them.

*Why a summary:* embeddings match numbers poorly, so the summary is what a conceptual query
lands on. The windows let a query that names a value or label land on its rows.

### One description prompt for non-PDF files
- **The call:** for CSV, TSV, TXT and MD, one LLM call (through the same `_resolve_llm_config`
  and reasoning-model fallback as citations) gets a 1–3 sentence description. It is sent
  the summary passage for tables, or the first ~2,000 words for text.
- **Where it goes:** the result is stored as the citation row, with
  `type: "citation", record: "dataset" | "note"`, `title` set to the file name and
  `abstract` set to the description. The other citation fields are left empty. For tables,
  the description is also placed into the summary passage before embedding, which is why
  the call runs before the chunks are built, matching the PDF order.
- **Status:** it reports through `citation_status` (`extracted | failed | disabled`), so the
  UI and the progress code need no new states.

*Alternative:* a separate `descriptions` collection. Rejected: every reader of `citations`
(hit formatting, the reconciler) would need a second lookup.

### Publication model
`Publication` gains `kind` (defaulting to `"pdf"`) and `row_count` (optional). The
description uses the existing `abstract` field. Both new fields are filled from chunk and
citation metadata by the reconciler, so stores and DB rows from earlier versions read as
PDFs. The `has_pdf` and `pdfs_dir` names stay as they are, since they are internal. The DB
stores publications as JSON, so there is no column change.

### Search hit format
`rag_mcp._format_results` builds the header from `metadata.kind`:
- **PDF:** unchanged.
- **CSV/TSV:** `[i] salts.csv, rows 51-100 (CSV)`.
- **Text:** `[i] notes.md, passage 3 (Markdown)`.

Non-PDF headers add `Full file: fetch_kb_file(kb_slug="<slug>", filename="<name>")`, and
show the description in place of a citation. The `rag_search` docstring drops "Do NOT use
this for quantitative lookups", and says instead that tabular hits point to a file to fetch
and compute on.

### `fetch_kb_file` as an agent tool, not a mount
It is an `@agent.tool_plain` next to `fetch_repository`. It works as follows:
- It checks `kb_slug` against `self.project.knowledge_bases` and loads the KB row for its
  files directory.
- It resolves `filename` inside that directory, rejecting `..` and anything that resolves
  outside it.
- It copies the file to `self.output_dir.parent / "work" / "kb" / slug / filename` (the
  sandbox's `/mnt/data/work/kb/<slug>/`), makes it readable to the sandbox user the way
  `fetch_repository` does, and returns the sandbox path and size.
- Errors raise `ModelRetry` with the reason. For an unattached slug, the message lists the
  attached slugs.

*Alternatives:*
- A read-only mount. Rejected: KBs attached mid-session would need a sandbox respawn.
- Copying on each search hit. Rejected: it gives a read-only tool side effects.

### BM25 corpus on delete
When delete-by-source removes rows and `bm25_corpus.json` exists, the corpus is rebuilt from
the remaining chunks (`_build_bm25_corpus`). Otherwise hybrid search would score ids that no
longer exist. Adding single files keeps today's behaviour.

### Regenerating vista-build-inputs
The regeneration follows `docs/releasing.md` § "Updating them", run from this branch once
indexing is implemented:
1. Build without `--vector-store`, so the store is built by this branch's `build_rag.py`.
2. Extract the store, commit it to `vista-build-inputs`, and push.
3. Bump `.github/build-inputs.env` in this MR.

The new inputs commit is inert until the pin moves, so it can be pushed before this MR
merges. Every older pin still resolves. Pushing to the inputs repo needs the user's explicit
go-ahead at that step.

## Risks / Trade-offs

- **[Description quality varies with the model] →** It is short, optional and never
  blocks indexing. The deterministic summary carries findability without it.
- **[Wide CSVs make huge summary and window passages] →** Truncate each window passage to
  the embedder's practical length (the same ~512-word bound text chunks use). Above about
  30 columns, list only the first columns in full and count the rest.
- **[`csv.Sniffer` guesses wrong on odd files] →** Fall back to the extension's delimiter.
  A file that parses to one column is still indexed, just less usefully.
- **[The regenerated AI-safety store differs beyond ids] →** The citation LLM calls run
  again and may word fields differently. Check the regenerated store with
  `build_local_package.sh --check`, and spot-check citations in the explorer before
  bumping the pin.
- **[Old-id rows linger in old stores] →** This is harmless by design, and they are
  replaced the first time each file is re-indexed.

## Migration Plan

- No database or store migration. Old stores work as they are (see Decisions: identity
  uses `source`).
- Rollback: revert the MR, which also reverts the pin. Stores written by the new code (with
  full-name ids) stay readable by the old code for search. The old code's id-based skip
  check would miss on them, so re-adding the same files would duplicate them. That is
  acceptable on a rollback.
