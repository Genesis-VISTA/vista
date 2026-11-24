## Context

Every request resolves a user through `services/auth.get_user` (a fixed dev email, or the
`X-Vista-User-Email` header; `501` in prod), and services take a `ServiceUser` for access
checks that, on a desktop install, always pass. Per-user configuration lives on the
`app_user` row, and four tables carry a `user_id` foreign key to it.

## Decisions

### D1: One row, created at startup

`LocalSettingsTable` (`local_settings`) has an integer primary key fixed at `1`. `init_db`
creates the row when it is missing, generating `install_id`; `services/local_settings.get`
returns it and never creates a second. A read path never writes, so a GET cannot race
startup into two rows.

### D2: The models keep the user models' shapes

`LocalSettingsUpdate` and `LocalSettingsPublic` are `UserSelfUpdate` and
`UserPublicWithConfig` without `id` and `is_admin`, plus `install_id` (read-only) and
`display_name`. The UI's field names and the write-only token rules are unchanged, so the
settings modal changes only its endpoint. The legacy columns nothing read
(`remote_hpc_jobs_dir`, `frontier_account`, `s3m_token`) are not carried over: the table is
new, so there is no column drop to avoid.

### D3: Code that needs settings reads the row, not a request identity

Agents, HPC tool metadata, `globus_auth`, `repo_tokens`, the campaign monitor and inference
resolution take a `LocalSettingsPublic` (or read the row) where they took a user. Background
work (campaign monitor, debates) reads the row when it runs instead of loading a job's owner,
so a credential changed in Settings applies to the next poll.

### D4: No migration

A database from before this change is not migrated or detected: its `user_id` columns are
NOT NULL foreign keys to `app_user`, so inserts into it fail, and the researcher deletes it
by hand. Agreed with the maintainer: VISTA is in early development and its data is not
kept, so migration code would be weight with no user.
`incompatible-data-reset` now detects such a database at startup and offers Back up and reset
in the startup window instead of deleting by hand.

### D5: The API path is `/settings`

`/users/me` → `/settings`, `/users/me/inference` → `/settings/inference`, and so on. No alias
is kept; the UI is the only client.

## Risks

- A developer upgrading must delete `data/vista.db`, losing chats and saved credentials.
  Accepted (D4).
- Anything reachable on the backend's port can now read and change settings without even a
  header. It always could in dev; the backend binds to localhost and is unchanged in that.
