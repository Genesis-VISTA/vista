# knowledge-bases Specification

## Purpose
Defines which files a knowledge base accepts, how each format is indexed and described for
search, what search results say about where a passage came from, and how the agent gets a
knowledge-base file into its sandbox to work on it.

## Requirements

### Requirement: Accepted file formats

A knowledge base SHALL accept PDF (`.pdf`), CSV (`.csv`), TSV (`.tsv`), plain text (`.txt`)
and Markdown (`.md`) files, matched by extension without regard to case. Upload SHALL reject
any other file and say which formats are accepted. A file SHALL keep its own extension when
stored. Every accepted format SHALL be subject to the same per-file upload size limit. Files
of an accepted format found in the knowledge base's folder on disk SHALL be listed like
uploaded ones.

#### Scenario: Data and notes upload alongside papers

- **WHEN** a researcher uploads `paper.pdf`, `salts.csv`, `runs.tsv`, `notes.txt` and `README.md` to a knowledge base
- **THEN** all five are stored under their own names and listed in the knowledge base

#### Scenario: Unsupported format is refused

- **WHEN** a researcher uploads `results.xlsx`
- **THEN** the upload is rejected with a message listing the accepted formats, and nothing is stored

#### Scenario: Extension is not rewritten

- **WHEN** a researcher uploads `salts.csv`
- **THEN** it is stored as `salts.csv`, not `salts.csv.pdf`

### Requirement: Tabular files are indexed as a summary and row windows

Indexing a CSV or TSV file SHALL produce one summary passage and a series of row-window
passages. The summary SHALL contain the file name, the column names, the number of data
rows, an inferred type per column, a few sample rows, and the file's description when one
was generated. Each row window SHALL hold a contiguous range of about 50 data rows, preceded
by the header row, and SHALL record its row range. At most about 200 row windows SHALL be
indexed per file. When a file has more rows than that covers, the summary SHALL say that
only the first rows are indexed for search and give the total row count. The delimiter
SHALL be detected from the file. A UTF-8 byte-order mark SHALL NOT appear in a column
name, and a quoted field containing the delimiter SHALL stay one field.

#### Scenario: A small CSV is fully searchable

- **WHEN** a CSV with a header and 120 data rows is indexed
- **THEN** the store holds one summary passage and three row-window passages covering rows 1–50, 51–100 and 101–120, each starting with the header row

#### Scenario: A large CSV is capped

- **WHEN** a CSV with 50,000 data rows is indexed
- **THEN** no more than about 200 row windows are indexed, and the summary passage states the total of 50,000 rows and that only the first rows are searchable

#### Scenario: Summary names the columns

- **WHEN** a search for a term that appears only in a CSV's column names is run against its knowledge base
- **THEN** the CSV's summary passage is among the results

#### Scenario: TSV and quoting

- **WHEN** a TSV file, or a CSV with a BOM and a quoted field containing a comma, is indexed
- **THEN** its columns and field values are split correctly, and no column name carries the BOM

### Requirement: Text files are indexed as text

Indexing a `.txt` or `.md` file SHALL split its text into passages the way PDF text is
split. Each passage SHALL record its position in the file, since such files have no pages.

#### Scenario: Notes are searchable

- **WHEN** a Markdown file is indexed and a search matches a sentence in it
- **THEN** a passage from that file containing the sentence is returned

### Requirement: Non-PDF files get a description

When a model credential is available, indexing a CSV, TSV, TXT or MD file SHALL ask the
model for a short description of what the file contains. The description SHALL be
included in a tabular file's summary passage. It SHALL also be stored as that file's
citation record, marked as a dataset (CSV, TSV) or a note (TXT, MD), so that every search
hit from the file shows it. When no credential is available, or the model call fails, the
file SHALL still be indexed without a description: a tabular file keeps its summary
passage, built from the file alone. PDF citation extraction SHALL be unchanged.

#### Scenario: Described with a credential

- **WHEN** a CSV is indexed with a model credential configured
- **THEN** its summary passage and its citation record both carry the model's description, and the record is marked as a dataset

#### Scenario: Indexed without a credential

- **WHEN** a CSV is indexed with no model credential
- **THEN** it is indexed with its summary and row windows, no model call is made, and its description is reported as disabled

### Requirement: A file's indexed content is identified by its file name

Everything a knowledge base's store holds for a file SHALL be identified by the file's full
name, extension included. Files that differ only in extension SHALL NOT overwrite each
other. Indexing a file that already has content in the store SHALL skip it, unless a
re-index is requested. A re-index SHALL replace the file's earlier content, leaving no
duplicates. This SHALL hold for stores built by earlier VISTA versions as well as new ones.

#### Scenario: Same stem, different formats

- **WHEN** `foo.pdf` and `foo.csv` are both indexed into one knowledge base
- **THEN** passages from both files are searchable, and neither file's passages or citation record replaced the other's

#### Scenario: Already-indexed file is skipped

- **WHEN** a CSV that is already indexed is submitted for indexing again without a re-index
- **THEN** it is reported as skipped, and no embedding or model call is made for it

#### Scenario: Re-index on a store from an earlier version

- **WHEN** a PDF in a store built by an earlier VISTA version is re-indexed
- **THEN** the store afterwards holds exactly one set of passages and one citation record for that PDF

#### Scenario: Adding to an earlier store does not redo its files

- **WHEN** a new file is added to a knowledge base whose store was built by an earlier VISTA version
- **THEN** only the new file is embedded, and the files already in the store are skipped

### Requirement: Deleting a file removes it from search

Deleting a file from a knowledge base SHALL remove its passages and its citation record
from the store, as well as the file on disk. Search SHALL NOT return passages from a
deleted file.

#### Scenario: Deleted CSV stops appearing

- **WHEN** a researcher deletes `salts.csv` from a knowledge base and then searches for a term that only it contained
- **THEN** no result comes from `salts.csv`

### Requirement: Search hits identify their source

Each search hit SHALL name its source file and where in the file it comes from: the page
for a PDF, the row range (or "summary") for a CSV or TSV, and the passage position for a
TXT or MD file. Each hit SHALL also state the file's format. A hit from a non-PDF file SHALL
say that the complete file can be fetched into the sandbox, naming the knowledge base and
file to fetch. The search tool's guidance to the agent SHALL NOT discourage using it to find
quantitative data held in tabular files.

#### Scenario: CSV hit header

- **WHEN** a search returns a row window covering rows 51–100 of `salts.csv` in knowledge base `salt-data`
- **THEN** the hit is headed with `salts.csv`, rows 51–100 and the CSV format, and says it can be fetched as `salt-data` / `salts.csv`

#### Scenario: PDF hit header is unchanged

- **WHEN** a search returns a passage from page 4 of a PDF
- **THEN** the hit names the PDF and page 4, with its citation as before

### Requirement: The agent can fetch a knowledge-base file into its sandbox

The agent SHALL have a tool that, given a knowledge-base slug and a file name, copies that
file, in full, into the project's sandbox under `/mnt/data/work/kb/<slug>/`. The tool SHALL
return the file's sandbox path. It SHALL copy the complete file whatever part of it was
indexed for search. It SHALL accept only knowledge bases attached to the current project.
It SHALL fail with a clear message, and copy nothing, when the knowledge base is not
attached, when the file is not in it, or when the file has been deleted from disk. A file
name SHALL NOT be able to reach outside the knowledge base's folder.

#### Scenario: Fetch and compute

- **WHEN** the agent fetches `salts.csv` from an attached knowledge base `salt-data`
- **THEN** `/mnt/data/work/kb/salt-data/salts.csv` exists in the sandbox, is byte-identical to the stored file, and the tool returns that path

#### Scenario: Rows past the index cap are available

- **WHEN** the agent fetches a CSV whose row windows were capped during indexing
- **THEN** the fetched file contains every row

#### Scenario: Unattached knowledge base

- **WHEN** the agent asks for a file from a knowledge base not attached to the current project
- **THEN** the tool fails with a message naming the project's attached knowledge bases, and nothing is copied

#### Scenario: Missing file or traversal

- **WHEN** the agent asks for a file that is not on disk, or a name such as `../other/secret.csv`
- **THEN** the tool fails with a clear message, and nothing is copied

### Requirement: The explorer shows each file's format and description

The knowledge-base explorer SHALL show each non-PDF file with its format, its description
when there is one, and, for CSV and TSV files, its number of data rows. The explorer's file
picker SHALL offer every accepted format. Downloading a file SHALL serve it with the
content type of its format.

#### Scenario: CSV in the explorer

- **WHEN** a researcher opens a knowledge base containing an indexed, described CSV
- **THEN** the CSV is listed with a CSV badge, its description and its row count

#### Scenario: Download content type

- **WHEN** a researcher downloads `salts.csv` from the explorer
- **THEN** it is served as `text/csv`
