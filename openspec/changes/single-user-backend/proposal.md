## Why

VISTA is a desktop app with one researcher per install, but the backend still carries the
multi-user scaffolding of the website it was once planned as: a dev-header identity and a
production `501` in `services/auth.py`, an `app_user` table with admin `/users` CRUD, and
project membership. None of it protects anything on a laptop, and all of it is in the way of
the collaboration server (`PLAN.md`, Step 3 on), whose identity is the install, not a login.

## What Changes

- **BREAKING**: one local settings row replaces `app_user`. It holds every per-user field
  today (inference provider and keys, S3M/IRI/Globus tokens, GitHub/GitLab tokens, cluster
  accounts and remote dirs, hidden clusters, research profile, email), still encrypted where
  it was, plus the collaboration identity stub: `install_id` (a UUID made on first run) and
  `display_name`.
- **BREAKING**: `services/auth.py`, `services/user.py`, the admin `/users` routes,
  `ServiceUser`, `ProjectMemberTable` and the member routes are removed, and no route needs
  an identity. `user_id` goes from chat sessions, campaign runs, HPC jobs and debate runs; the
  agent pool is keyed by `(session_id, project_id)` and the sandbox volume is
  `volumes/{project_id}`.
- **BREAKING**: `/users/me*` becomes `/settings*` with the same shapes (including
  `inference`, `hpc-status` and `globus/{cluster}/*`). The UI follows: `lib/user.ts` becomes a
  settings client, its proxy routes move under `app/api/settings/`, the members modal goes,
  and the dead `x-amzn-oidc-*` forwarding is dropped.
- **BREAKING**: a database from before this change does not work with it and is not
  migrated: delete it by hand (`data/vista.db`) and start fresh. VISTA is in early
  development, and its data is not kept.
- Tests: the user-referencing backend tests are rewritten; tenant-isolation, dev-identity and
  membership tests are deleted; `loadgen.py --users` and the identity header go from
  `backend/scripts/`; `docs/evaluation-runbook.md` Phase D follows.

## Capabilities

### New Capabilities

- `local-settings`: one installation, one settings row; where it lives, how it is read and
  written, and the install identity.

### Modified Capabilities

- `hpc-availability`: the cluster visibility choice persists in the installation's settings
  rather than per user.
- `testing-ci`: tenant isolation tests no longer exist to remain.

## Non-goals

- The collaboration server itself, or any use of `install_id` and `display_name` beyond
  storing them (Step 3).
- Authentication of any kind.
- Migrating, or detecting, an existing database.
- VISTAGuard.
