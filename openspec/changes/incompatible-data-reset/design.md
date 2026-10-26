## Context

See proposal.md for why. Today `init_db()` (`backend/src/vista_backend/db/db.py`), called from
the FastAPI lifespan, runs `create_all` and then `_add_missing_columns`. The latter adds missing
nullable columns and skips anything else with a warning. Nothing checks whether the database can
actually serve the models. A v0.1.4 database still has NOT NULL `user_id` columns and a NOT NULL
`skill.is_public` that main never writes, and lacks four NOT NULL booleans, so it fails inside
the API.

The package launchers start each service and poll its health URL, for up to 600 s for the
backend, so a backend that exits at once is reported as `health-timeout` ten minutes later. The
Electron startup window (`electron/src/startup.html`, `startup-renderer.js`,
`startup-preload.cjs`, with handlers in `main.js`) shows a failure's message from
`FAILURE_MESSAGES` in `startup-protocol.js` and offers Try Again, Open Logs, Copy Diagnostics and
Quit. `main.js` already knows the state directory (`VISTA_HOME`, else `~/.vista`, or
`~/.vista-dev` for a development app).

What a state directory holds, as seen in a real install:

| Entry | What it is | Kept by a reset? |
|---|---|---|
| `vista.db` (+ `-wal`, `-shm`) | projects, conversations, jobs, debates, settings and tokens | moved |
| `knowledge-bases/<slug>/` | each knowledge base's PDFs and index | moved |
| `storage/<uuid>/` | skill folders, each with a `SKILL.md` naming it | moved |
| `volumes/<project-id>/` | each project's inputs and outputs | moved |
| `forum-git/`, `hpc_job_registry.json`, Globus and other folders | other researcher state | moved |
| `huggingface/` (~0.5 GB) | downloaded models (`HF_HOME`) | kept |
| `microsandbox/` (~1 GB) | sandbox runtime and image (`MSB_HOME`) | kept |
| `vista-data/` (~0.3 GB) | the bundled data payload (`VISTA_DATA_PAYLOAD_DIR`) | kept |
| `logs/` | service and window logs; `window.log` is open | kept |

The launcher re-extracts any bundled corpus part missing from the state directory, from the
package's own `payload.tar` and offline (`package_launcher.sh`, "Each part is skipped when
already present"), so the bundled knowledge bases come back by themselves after a reset.

The unmerged `db-migrations` branch already built and tested two pieces this change needs:
- the schema check (`db/schema_check.py`, `bae3f97b`);
- the startup-refusal plumbing from backend to launcher to window (`dadfdd15`).

## Goals / Non-Goals

**Goals:**

- One check, on every start, that names an incompatible database before anything writes to it.
- The researcher recovers from the startup window, keeps every piece of their work in one
  folder, and is told where it is.
- The smallest surface that does this: no versioning, no migration code, no deletion.

**Non-Goals:**

- Telling "older" from "newer" databases. Both are just incompatible.
- Reading anything back from a backup.

## Decisions

### D1: The schema check decides compatibility

Port `db/schema_check.py` from `db-migrations` as is. It compares a live SQLite database, over a
plain `sqlite3.Connection`, with `SQLModel.metadata`. It reports `table.column` problems:

- a model table or column the database lacks, except what `create_all` and the column-adder can
  add when `assume_additive=True`, namely missing tables and missing nullable or server-default
  columns;
- a NOT NULL column without a default that the models don't declare, which makes every insert
  fail.

It ignores types, column order, extra nullable columns and unknown tables.

**Alternative considered:** a schema version in `PRAGMA user_version`, bumped by hand on each
incompatible change. It is cheaper to check, but it adds a discipline the team would have to
keep, and it fails silently when someone forgets to bump it.

### D2: `init_db` checks before the engine exists

For a SQLite file database, `init_db` opens a stdlib connection, before `get_engine()`, and
handles three cases:

- **No file, or an empty one:** proceed as today.
- **Otherwise:** run the check with `assume_additive=True`.
- **Any problem:** write the backup index (D6), then raise `DatabaseIncompatible(message)`.
  Nothing has been written to the database. The message names the path, the problems, and the
  remedy for a developer ("move the state directory's data aside, or delete `vista.db`, to start
  fresh").

In-memory and non-SQLite URLs skip the check. After a passing check, `create_all`, the
column-adder and seeding run unchanged.

### D3: One code, `database-incompatible`, from backend to window

Port the plumbing from `dadfdd15`, with its two codes collapsed into this one:

- **Backend.** The lifespan catches `DatabaseIncompatible`, logs it, writes
  `database-incompatible` to `VISTA_STARTUP_FAILURE_FILE` when that is set, and re-raises.
  uvicorn exits with status 3.
- **Launchers.** `package_launcher.sh` and `.ps1` clear and export `$LOGS/startup-failure`, and
  pass each service's PID to `wait_for` / `Wait-For`. A service that exits before it is healthy
  fails at once, with the file's code if it is a safe token, or `startup-error` otherwise. The
  timeout path still emits `health-timeout`.
- **Electron.** `FAILURE_MESSAGES['database-incompatible']` = "Your local data is incompatible
  with this version of VISTA, please reset." The protocol test that reads the launchers also
  reads the backend's code constant.

### D4: The startup window offers one action, Back up and reset, for that code only

- **Snapshot.** The renderer learns the failure's code from the snapshot it already receives.
  `failure.code` exists in `startup-protocol.js`; expose it to the renderer if it isn't already.
- **Button.** `startup.html` gains Back up and reset (primary), shown only when
  `status === 'failed'` and the code is `database-incompatible`. For that code, Try Again is
  hidden, because retrying would fail the same way. Open Logs, Copy Diagnostics and Quit stay.
- **Wiring.** The preload exposes `resetData()`. `main.js` handles `startup:reset-data` with the
  same `fromStartupWindow` guard as the other handlers.

**Room for a follow-up.** A later change may let the startup window fetch a signed
data-upgrade script attached to this version's GitHub release, and offer Upgrade my data
beside Back up and reset. To leave room for that:
- the failure section's actions are rendered from a list the main process supplies for the
  code, not hard-coded to one button;
- the backup step in `data-reset.js` is callable on its own, so an upgrade can back up first in
  exactly the same way.

Nothing else here anticipates it.

**Alternative considered:** a second action, Remove and reset, that deletes. It was dropped:
moving is as fast, loses nothing, and leaves one decision for the researcher instead of two.

### D5: The reset moves everything but caches and logs into `backups/<UTC>/`

The `startup:reset-data` handler does nothing, and returns false, unless:

- the request came from the startup window;
- the launcher controller's current failure code is `database-incompatible`;
- the launcher has finished stopping (`child === null`, the same condition that enables
  Retry).

Then:

1. **Confirm.** Show `dialog.showMessageBox` on the startup window, with Back up and reset and
   Cancel, Cancel as the default. It says that VISTA will move the local data (conversations,
   projects, settings, skills, knowledge bases and project files) into a backup folder in the
   VISTA data folder and start fresh, and that connections in Settings need setting up again.
2. **Move.** Create `<state>/backups/<UTC yyyymmddThhmmssZ>/`, then rename into it every
   top-level entry of the state directory except a frozen keep-list: `backups`, `logs`,
   `huggingface`, `microsandbox` and `vista-data`.
   - Unknown or future entries are moved. The safe default for anything unrecognised is to keep
     it with the researcher's data.
   - Every rename stays on the same filesystem, so it is instant whatever the size.
   - No service is running, and `window.log`, the one open file, stays in `logs/`. So Windows'
     refusal to rename open files doesn't bite.
3. **README.** Write `README.txt` into the backup folder (D6).
4. **Tell the researcher.** Show a second message box: "Your previous VISTA data is in `<path>`.
   Your skills, knowledge bases and project files are there; see README.txt." It offers Show
   Folder (`shell.openPath`) and Continue.
5. **Restart.** Call `launcherController.retry()`. The launcher reinstalls the bundled corpora,
   the backend creates and seeds a fresh database, and VISTA opens as on a first run.

If any rename fails partway, the handler stops. It moves back what it already moved, newest
first, and leaves the failure on screen with a message of its own, logging the error to
`window.log`. A half-reset state directory is never started on.

The logic lives in a small module, e.g. `electron/src/data-reset.js`, taking the state directory
and a clock and returning what it moved. That keeps it unit-testable without Electron.

**Alternatives considered:**
- *Moving the caches too.* That costs about 1.8 GB of re-download or re-extraction and needs a
  network for the models, to back up files that are not the researcher's.
- *A sibling folder (`~/.vista-backup-…`).* That is outside the one folder VISTA documents, and
  harder to find and to clean up.

### D6: Where things are in a backup: the README and the backend's index

Skills and project files sit under ids (`storage/<uuid>/`, `volumes/<project-id>/`), so the
folder alone doesn't say whose is which. When the backend refuses a database (D2), it writes a
best-effort `<state>/incompatible-data-index.json` beside it, over the same stdlib connection,
and before raising. The index holds:

- projects: id and name;
- skills: name and folder path;
- knowledge bases: slug and name.

Each query is guarded, so a table or column that is missing in some old version leaves that part
out instead of failing. Writing the index never changes the database.

The reset moves the index with everything else. `data-reset.js` writes `README.txt` from it, or,
without it, from what it can see on disk:

- the database: `vista.db`, readable with the VISTA release that wrote it;
- each knowledge base: its PDFs in `knowledge-bases/<slug>/pdfs/`, ready to upload again;
- each skill: its name and folder under `storage/`, ready to import again from Skills;
- each project: its name and `volumes/<project-id>/`, holding its inputs and outputs;
- how to go back: install the earlier VISTA release and move the folder's contents back into the
  VISTA data folder.

Without the index, skill names come from each folder's `SKILL.md`, and projects are listed by id
only.

**The researcher's work first.** Each index entry carries `comes_with_vista`. The backend sets it
from what the current code ships:
- a skill whose name is a bundled skill (`db/skills/`);
- a project or knowledge base whose id is one that seeding creates (`db/seed.py`).

The README then lists only entries whose folder exists in the backup. It lists the researcher's
own skills, knowledge bases and projects first. VISTA's own come after, under one line saying
they come back with VISTA and need no restoring. Without the index, nothing is marked, and
everything found on disk is listed as the researcher's.

**A restore prompt at the head of the README.** The README opens with a short paragraph and a
prompt the researcher can paste into a coding agent such as Claude Code or Codex. The agent
restores the researcher's own work through the new VISTA's HTTP API (`http://127.0.0.1:8001`,
schema at `/openapi.json`, port overridable with `VISTA_BACKEND_PORT`), not by writing to any
database. The API is the contract the new version keeps, whatever changed in the schema. The
prompt:

> This folder is a backup that VISTA made when it found my local data incompatible with the
> version now installed. Read README.txt in this folder first. Then help me restore my own
> work into the running VISTA, whose backend API is at http://127.0.0.1:8001 (its schema is at
> /openapi.json).
>
> Rules:
> - Never change, move or delete anything in this backup folder. Read vista.db only from a copy
>   you make outside it, with sqlite3.
> - Write only through the running VISTA's HTTP API. Never edit its database or data folder
>   directly.
> - Restore only the items README.txt lists as mine, not the ones it says come back with VISTA.
> - In this order:
>   - knowledge bases: create each and upload its PDFs;
>   - skills: import each skill folder;
>   - projects: recreate each with its description, instructions, skills and knowledge bases,
>     then upload the files from its volumes folder to the project;
>   - conversations: recreate them if the API allows it, otherwise save each one as a Markdown
>     file in its project's files.
> - Don't restore settings, API keys or credentials. I'll reconnect those in Settings.
> - Before writing anything, show me what you plan to restore and wait for my go-ahead. At the
>   end, tell me what was restored, what wasn't, and why.

`data-reset.js` writes it verbatim, with the port filled in from `VISTA_BACKEND_PORT` when
`main.js` has it, so the README matches the install.

**Alternative considered:** a restore built into VISTA. That is a migration in all but name,
which is what the team ruled out. A prompt costs nothing to ship, adapts to whatever the old
data holds, and stays with the researcher's own agent and judgement.

### D7: After a reset, startup is a first run

The state directory holds only the caches, the logs and `backups/`. The launcher reinstalls the
bundled corpus parts, and the backend creates and seeds a database as on a first install. A task
verifies this end to end on a package, including that the bundled knowledge bases are searchable.

### D8: Tests

- **Backend:**
  - port `test_schema_check.py`;
  - new `init_db` tests: refusal before any write, with file bytes unchanged, for a missing NOT
    NULL column, an unwritten NOT NULL column and a v0.1.4 database; start as before for a
    fresh, an additive and an extra-nullable database;
  - the index's contents, including a database missing one of the queried tables;
  - a lifespan test for the failure file.

  The v0.1.4 case uses `backend/tests/fixtures/db/v0.1.4.sql` from `db-migrations` (`4caf8a99`)
  as a frozen sample of a real release database. Its capture script isn't brought over.
- **Launchers:** port the supervised-test cases from `dadfdd15`, so an exiting backend yields
  `database-incompatible`, and a silent exit yields `startup-error`, well before the timeout.
- **Electron:**
  - the protocol test (code and message);
  - unit tests of `data-reset.js` on a temp state directory: what moves and what stays, an
    unknown entry moved, a second reset beside an earlier backup, rollback when a rename fails,
    and the README with and without the index;
  - the handler's guards.

  An e2e case in `startup.e2e.js` with the fake launcher emitting `database-incompatible` is
  manual or nightly, per the validation lane, not PR CI.

## Risks / Trade-offs

- **The researcher loses their working state, even when only one table is incompatible.** That
  covers projects, conversations, settings and tokens. → That is the decision: no migrations
  during beta. Everything is kept in one folder with a README, and the confirmation says so
  before anything moves.
- **The check is stricter than the column-adder.** A developer whose branch adds a NOT NULL
  column without a server default now gets refused on their existing dev database, where today
  they'd get a 500 later. → It is the same failure, reported earlier. A server default makes the
  change compatible, and AGENTS.md says so.
- **The keep-list can go stale.** A new cache folder added later would be moved by a reset and
  rebuilt on the next start. → That costs time, never data. The list sits next to a comment
  naming where each cache is configured.
- **Backups accumulate.** → They are the researcher's to delete. The README says the folder can
  be removed once nothing in it is needed.
- **A development app (`~/.vista-dev`) is reset the same way.** → That is intended: the same
  code path, on the dev state directory.

## Migration Plan

None needed. The first start of this release on a v0.1.4 database shows the reset prompt. To roll
back, install the previous release and move the backup folder's contents back into the state
directory.
