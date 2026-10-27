## Why

A researcher on v0.1.4 who installs a newer build cannot use VISTA. The backend starts on a
`vista.db` it can no longer write to, and the failure shows up later inside the API, or in a
package as a generic `health-timeout` ten minutes in. The team has decided against database
migrations while VISTA is an early beta. So an incompatible installation should be named at
startup, and the researcher should be able to start fresh from the startup window while keeping
their old data somewhere they can find it.

## What Changes

- At every start, before writing anything, the backend checks that `vista.db` can serve the
  current models. One it can't is refused, untouched, with the startup failure code
  `database-incompatible`. A new or missing database starts as today.
- The package launchers report a service that exits during startup at once, with the code the
  backend left, instead of waiting out the health timeout.
- For `database-incompatible`, the startup window says "Your local data is incompatible with
  this version of VISTA, please reset" and offers **Back up and reset**. After a confirmation:
  - it moves all of the researcher's local data (database, knowledge bases, skills, project
    inputs and outputs) into a dated backup folder;
  - it starts VISTA fresh;
  - it says where the backup is, and that their skills, knowledge bases and project files are
    there.

  A README in the backup explains its layout. Caches (models, the sandbox runtime, the bundled
  data payload) and logs stay, so the restart needs no download or rebuild.
- A developer running without the window gets the same refusal in `backend.log`, with the
  remedy.
- This replaces `single-user-backend`'s "delete it by hand" with the prompt.

## Capabilities

### New Capabilities

- `local-data-compatibility`: how the backend decides at startup that an installation's database
  is incompatible, and what it does then.

### Modified Capabilities

- `desktop-window`: "Startup failures are actionable in the application" gains an exited service
  and incompatible local data. A new requirement covers the reset.
- `laptop-distribution`: "Mutable state lives outside the artifact". Replacing the artifact keeps
  state when it is compatible, and otherwise leads to the reset prompt.

## Impact

- Backend: `db/db.py` (`init_db`), a new `db/schema_check.py`, `api/api.py` (lifespan), and
  tests.
- Launchers: `scripts/package_launcher.sh` and `.ps1`, and their supervised tests.
- Electron: `startup-protocol.js`, `main.js`, a new reset module, the startup window's HTML,
  renderer and preload, and tests.
- Docs: one line in `AGENTS.md`.
- Reuses, adapted, the schema check and the startup-refusal plumbing from the unmerged
  `db-migrations` branch.

## Non-goals

- Migrations, or carrying any data across an incompatible change.
- Re-importing from the backup, or restoring it from the window. The README says how to do both
  by hand.
- Deleting data. A reset only moves it.
- Schema versioning, or treating a database from a newer VISTA as a case of its own.
- VISTAGuard.
