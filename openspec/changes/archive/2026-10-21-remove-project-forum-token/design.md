## Context

`repo_token(url, user, project)` (`services/repo_tokens.py`) picks the token git sends to a
forum: the project's `forum_repo_token`, else the user's Settings token for github.com or
gitlab.com, else none (the system git's credentials). Only the forum passes a project.
This change removes the first branch so a project row holds no secret.

## Decisions

### D1: Remove, don't migrate

Startup leaves the column in existing databases: `create_all` never drops one. No code reads
it, but it may still hold an encrypted token, so `scripts/migrate_columns.py` lists it in
`RETIRED` and its reconcile drops it. A researcher who relied on a project token for a
GitHub or gitlab.com forum adds a Settings token; on any other host, an SSH key or a
credential helper. No backwards compatibility is kept.

### D2: An old failure record naming `project` is ignored

A forum's last failed publish is stored on disk with the credential that was sent. One
written before this change may say `project`. `PublishError` drops `project` from its
credential values, so `_publish_error` already returns None for such a record (it treats a
record it cannot read as none), and the next failed publish writes a current one.

### D3: The dialog keeps saying which credential applies

The token field goes, but the note under the forum URL stays: it names the Settings token
that will be sent, or the system git's credentials, mirroring `repo_token`. The token-help
link (a forge's token form pre-filled for one repository) goes with the field: a Settings
token is account-wide, and Settings → Repositories already links to the forge's form.

## Risks

- A forum on a self-hosted GitLab that relied on a project token stops publishing until the
  researcher sets up SSH or a credential helper. The lab says the system git's credentials
  were refused, which names the fix.
