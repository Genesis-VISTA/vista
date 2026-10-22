# Hands-on plan: kb-ingest-other-formats

Run against `./launch.sh logs` with a real model credential. Record outcomes in
[Results](#results) (task 6.3).

## Generate the fixture

```bash
uv run scripts/make_kb_csv_fixture.py ~/Downloads/vista-kb-fixture
```

It writes six KB files and `expected.json` (also printed). The run is
deterministic. In `expected.json`, rows are 1-based data rows, header not counted.

## Setup

1. In the Knowledge Bases explorer, create a KB named `salt-fixture`.
2. Upload all six files: `flibe_viscosity.csv`, `flibe_viscosity.txt`,
   `flinak_density.tsv`, `salt_melting_points.csv`,
   `lif_thermal_conductivity_long.csv`, `measurement_notes.md`.
3. Try to upload any `.xlsx`: it must be refused with a clear message.
4. Attach `salt-fixture` to a project.
5. Wait for indexing, then check the explorer:

| File | Badge | Row count |
|---|---|---|
| `flibe_viscosity.csv` | CSV | 151 |
| `flinak_density.tsv` | TSV | 151 |
| `salt_melting_points.csv` | CSV | 12 |
| `lif_thermal_conductivity_long.csv` | CSV | 25,000 (not 10,000) |
| `flibe_viscosity.txt` | Text | - |
| `measurement_notes.md` | Markdown | - |

- Every file shows a description once indexed.
- `flibe_viscosity.csv` and `flibe_viscosity.txt` are both listed and both indexed.

## Prompts and checks

Open a new chat in the project for each prompt. For `rag_search` hit headers and
`fetch_kb_file` calls see [Inspecting tool outputs](#inspecting-tool-outputs).

| # | Prompt | `rag_search` should return | `fetch_kb_file` | Compare with `expected.json` | Plot |
|---|---|---|---|---|---|
| 1 | "What's FLiBe's viscosity at 923 K? Plot viscosity vs temperature." | `flibe_viscosity.csv, rows 51-100 (CSV)` (key `flibe_viscosity_923K_expected_hit`; 923 K is data row 76) or its `summary (CSV)` hit | Must call it for `flibe_viscosity.csv`: the plot needs all 151 rows | `flibe_viscosity_at_923K_mPa_s` = 6.7808 mPa s | Yes, 151 points |
| 2 | "What's the melting point of the salt with a comma in its name?" | `salt_melting_points.csv, rows 1-12 (CSV)` (key `comma_salt_expected_hit`) or its summary | Optional | `comma_salt_name` = `LiF-BeF2, 2:1 (FLiBe)` in one piece, `comma_salt_melting_point_K` = 732 | No |
| 3 | "Plot LiF thermal conductivity across the whole dataset and report the value at row 20,000." | `lif_thermal_conductivity_long.csv` hits, windows only up to `rows 9951-10000`; the summary says 25,000 rows | Must call it: row 20,000 is past the index cap (10,000 rows), so search cannot contain it | `lif_row_20000`: row 20000, T = 1349 K, conductivity 99.99 W/m K, flag `MARKED`; `lif_total_rows` = 25000 | Yes, all 25,000 rows, with the 99.99 outlier visible |
| 4 | "Which file has FLiBe data, according to the notes?" | `measurement_notes.md, passage 1 (Markdown)` (key `notes_expected_hit`) | Optional | `notes_name_flibe_file` = `flibe_viscosity.csv` | No |

Also check in every run: each non-PDF hit shows a `Full file: fetch_kb_file(...)`
line and, where the file has one, a `Description:` line; no hit reads
`unknown, page ?`.

## Delete check

1. In the explorer, delete `flibe_viscosity.csv`.
2. New chat, re-ask prompt 1.
3. Check:
   - no `rag_search` hit names `flibe_viscosity.csv`;
   - no blank `unknown, page ?` hits;
   - `fetch_kb_file` refuses `flibe_viscosity.csv` (file not in the KB);
   - `flibe_viscosity.txt` is still found by a search for "FLiBe viscosity
     measurement method".

## Inspecting tool outputs

The chat UI shows only "Calling `rag_search`...". The raw tool returns are in the
chat's `message_history` in the app DB; ask Claude to read them for the chat in
question.

## Results

| Check | Outcome | Notes |
|---|---|---|
| Setup (badges, descriptions, row counts, `.xlsx` refused) | | |
| Prompt 1: FLiBe viscosity at 923 K, plot | | |
| Prompt 2: comma-named salt | | |
| Prompt 3: LiF whole dataset, row 20,000 | | |
| Prompt 4: file named in the notes | | |
| Delete check | | |
