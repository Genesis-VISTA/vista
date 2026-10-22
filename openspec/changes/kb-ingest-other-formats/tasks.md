## 1. Extraction by format (`build_rag.py`)

- [x] 1.1 Add the suffix → extractor table (`.pdf`, `.csv`, `.tsv`, `.txt`, `.md`, case-insensitive), exported so the backend can import the accepted-format list, and move PDF extraction behind it, setting `metadata.kind="pdf"`. Verify that a new `backend/tests/test_build_rag_formats.py` asserts the table's suffixes and that a small PDF made with PyMuPDF extracts as before (`source`, `page`, `chunk_id`, `kind`)
- [x] 1.2 Implement the CSV/TSV extractor per design.md:
  - `utf-8-sig`, `csv.Sniffer` with an extension fallback, per-column int/float/text inference;
  - a summary passage, then 50-row header-prefixed windows capped at 200, with a truncation note and the total row count;
  - `rows` metadata, and the wide-file bounds from design.md's risks.

  Verify in `test_build_rag_formats.py`:
  - 120 rows → summary + windows `1-50`, `51-100`, `101-120`, each starting with the header;
  - a 50,000-row file → ≤200 windows and a summary stating 50,000 rows and the truncation;
  - a BOM-prefixed CSV with a quoted comma, and a TSV, parse into the right columns.
- [x] 1.3 Implement the TXT/MD extractor with the existing `_chunk_text`, setting `kind` (`text` / `markdown`) and `chunk_id`. Verify in `test_build_rag_formats.py` that a Markdown file's sentence lands in one of its passages
- [x] 1.4 Add the description call for non-PDF files: same LLM resolution and fallback as citations, input as in design.md, result stored as a citation row (`record: dataset | note`, `title`=file name, `abstract`=description) and inserted into a table's summary before embedding; status via `citation_status`. Verify with a stubbed `send_prompt_to_chatgpt`: with credentials, the summary and citation row both carry the description; with `llm_credentials` disabled, no call is made and the status is `disabled`
- [x] 1.5 Rename `index_single_pdf` to `index_single_file`, routing through the table (citation for PDFs, description for the rest), and update the batch `index_pdfs` to scan every accepted suffix. Verify `uv run --extra dev pytest tests/test_build_rag_formats.py tests/test_indexer_paths.py` in `backend/` passes

## 2. Identity keyed on source (`build_rag.py`)

- [x] 2.1 Switch chunk and citation ids to `<filename>_text_<j>` / `<filename>_citation`. Verify in a new `backend/tests/test_build_rag_identity.py` (real Chroma in `tmp_path`, embedder stubbed to fixed vectors) that indexing `foo.pdf` and `foo.csv` leaves both files' chunks and citation rows present
- [x] 2.2 Replace the id-based skip check with `text_chunks.get(where={"source": ...}, limit=1)`, and make a forced re-index delete by source from both collections before writing. Verify in `test_build_rag_identity.py`:
  - a second index of a CSV returns `skipped` with no embed call;
  - a store seeded with old-style `<stem>_...` rows for a PDF, re-indexed, ends with exactly one set of chunks and one citation row for it;
  - adding a new file to that old store embeds only the new file.
- [x] 2.3 Add a public `delete_source(filename)` that removes the file's rows from both collections and rebuilds `bm25_corpus.json` if it exists. Verify in `test_build_rag_identity.py` that after deletion no row has that source, and that an existing BM25 corpus no longer lists its ids

## 3. Backend API and indexer

- [x] 3.1 Use the accepted-format list in `api/knowledge_bases.py`:
  - filename sanitising keeps the extension and rejects other suffixes with a message listing the formats;
  - `_reconcile_with_disk` scans every accepted suffix;
  - downloads use `mimetypes` (`text/csv`, `text/tab-separated-values`, `text/plain`, `text/markdown`, `application/pdf`).

  Verify in `backend/tests/test_knowledge_base_upload.py`: uploading `.pdf`, `.csv`, `.tsv`, `.txt` and `.md` stores each under its own name; `.xlsx` is rejected and nothing is stored; downloading a CSV returns `text/csv`.
- [x] 3.2 Make `utils/indexer.py` index every format through `index_single_file`, and add a `delete_publication_rows(rag_db_path, filename)` that runs `delete_source` under the per-DB lock, reusing the cached `TextRAG`. Verify a test in `test_indexer_paths.py` that a non-PDF filename is passed through, and that deletion takes the lock
- [x] 3.3 Call the row deletion from `delete_publication`, so the file's stub no longer reappears after reconciliation. Verify in `test_knowledge_base_upload.py` (indexer faked) that delete invokes the row deletion for that filename, and that the KB read afterwards no longer lists it
- [x] 3.4 Add `kind` (default `"pdf"`) and `row_count` to `Publication` and `ui/lib/types.ts`, filled by `_merge_chroma_metadata_into_pub` / `_merge_citation_into_pub` from chunk and citation metadata, with the description in `abstract`. Verify a reconciler test that a CSV's citation row yields `kind="csv"`, its row count and its description, and that a row without `kind` reads as `pdf`

## 4. Search hits and the fetch tool

- [x] 4.1 Make `rag_mcp._format_results` head hits by kind:
  - PDF unchanged;
  - `rows 51-100 (CSV)` / `summary (CSV)`;
  - `passage N (Markdown|Text)`.

  Non-PDF hits get the description and a `fetch_kb_file(kb_slug=…, filename=…)` line, and the `rag_search` docstring loses "Do NOT use this for quantitative lookups" in favour of the fetch guidance. Verify with a new `mcp_servers/vista_mcp_server/tests/test_rag_format_results.py` covering a PDF, a CSV window, a CSV summary and a Markdown hit.
- [x] 4.2 Add `fetch_kb_file(kb_slug, filename)` beside `fetch_repository` in `agents/agents.py`, per design.md. Verify in a new `backend/tests/test_fetch_kb_file.py`:
  - an attached KB's CSV is copied byte-identical to `<volume>/data/work/kb/<slug>/<file>`, and the sandbox path is returned;
  - an unattached slug fails with the attached slugs listed;
  - a missing file and `../x.csv` each fail with nothing copied.
- [x] 4.3 Mention `fetch_kb_file` in the system prompt's knowledge-base block next to the slug list. Verify the existing system-prompt test (or a new assertion in `test_fetch_kb_file.py`) that the block names the tool when the project has KBs

## 5. UI

- [x] 5.1 Widen both upload pickers in `ui/app/knowledge-bases/explorer.tsx` (`accept` and the two non-PDF checks) to the accepted formats, and update the "PDFs:" folder label. Verify with `cd ui && npm run lint` and a manual upload of a CSV in the explorer
- [x] 5.2 Show a format badge, the description and (for CSV/TSV) the row count for non-PDF publications, keeping PDFs as they are, and verify with a hermetic Playwright screenshot of a KB fixture containing a PDF and a CSV
- [x] 5.3 Run `./scripts/ci-local.sh` (all lint + test) and verify that it passes. Note that the UI lane rebuilds `ui/.next` and stops any `launch.sh` stack running in this checkout

## 6. Hands-on test (manual, not in PR CI)

- [x] 6.1 Add `scripts/make_kb_csv_fixture.py` (stdlib only; run with `uv run scripts/make_kb_csv_fixture.py <out-dir>`), which writes a synthetic KB with known values:
  - `flibe_viscosity.csv`: T_K 773–1073 step 10, viscosity from a fixed formula;
  - `flinak_density.tsv`: tab-separated;
  - `salt_melting_points.csv`: a dozen salts with BOM and quoted names containing commas;
  - `lif_thermal_conductivity_long.csv`: 25,000 rows, past the window cap, with a marked value at row 20,000;
  - `measurement_notes.md`: says which file holds the FLiBe data;
  - `flibe_viscosity.txt`: the same stem as the CSV.

  It also prints `expected.json`, holding the target values the run below checks. Verify that running it twice produces identical files.
- [x] 6.2 Write the hands-on plan as `openspec/changes/kb-ingest-other-formats/hands-on.md`. It covers:
  - **Setup:** create a KB `salt-fixture` from the fixture, attach it to a project, and confirm in the explorer that badges, descriptions and row counts are shown;
  - **Prompts to run:**
    - "What's FLiBe's viscosity at 923 K? Plot viscosity vs temperature."
    - "What's the melting point of the salt with a comma in its name?"
    - "Plot LiF thermal conductivity across the whole dataset and report the value at row 20,000."
    - "Which file has FLiBe data, according to the notes?"
  - **Checks for each prompt:**
    - `rag_search` returns the right file (and the right row window where one exists);
    - the agent calls `fetch_kb_file`;
    - the plotted and reported values match `expected.json`, including the row past the cap;
    - deleting `flibe_viscosity.csv` makes searches stop returning it.

  Verify that the plan is reviewed by Sam.
- [ ] 6.3 Run the hands-on plan against `./launch.sh logs` with a real model credential, and record the outcome per prompt in `hands-on.md`. Verify that every check passes, or that a failure is recorded with its cause

## 7. Release build inputs

- [ ] 7.1 Regenerate the AI-safety store from this branch per `docs/releasing.md` § "Updating them": build without `--vector-store`, then extract `rag_db`. Verify with `./scripts/build_local_package.sh --check --payload <inputs>/vista-data --vector-store <new rag_db>`, and spot-check that its chunk ids use full file names and that citations look right in the explorer
- [ ] 7.2 **Ask Sam before pushing.** Commit the new `rag_db` to `Genesis-VISTA/vista-build-inputs`, push (never force), and record the full commit hash. Verify that `git ls-remote` shows the commit on its default branch
- [ ] 7.3 Set `BUILD_INPUTS_COMMIT` in `.github/build-inputs.env` to that hash in this MR, and verify by re-running the `--check` from 7.1 against a fresh clone at that commit
