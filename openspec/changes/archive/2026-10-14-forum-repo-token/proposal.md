## Why

A Hypothesis Lab forum lives in a git repository, and VISTA pushed to it only with the
researcher's own git credentials: an SSH key in their agent or a credential helper. A private
GitHub or GitLab repository over HTTPS therefore needed git set up outside VISTA, and a
researcher launching the app from the Dock had no way to give it access (#75).

Separately, the first view of a new debate could fail with a 500. The debate task and the
viewer's refresh both copy the thread into the database, each from its own session. One could
store a post between the other's read and its insert, and the refresh's rollback left the
debate unreadable.

## What Changes

- A project may store an access token for its forum repository. It is entered in the project
  dialog, stored encrypted, never read back, and sent only to that repository's `https://` URL
  on fetch and push. Without one, git uses the researcher's own credentials as before.
- The dialog's "Don't have a token?" opens the forge's token form pre-filled for the
  repository (GitHub fine-grained token with Contents write; GitLab with `read_repository` and
  `write_repository`), with the steps the form cannot pre-fill.
- Copying a thread into the database tolerates a post another writer stored first, and a
  failed refresh no longer breaks the debate's read.

## Capabilities

### Modified Capabilities

- `agent-forum`: the save-time check of the forum remote accepts the project's token; new
  requirements for the token and for the race-tolerant projection.

## Non-goals

- Tokens for importing skills from a private repository (the other half of #75). That path
  still uses the backend's `github_token` setting.
- Per-user tokens. VISTA is a desktop application; one install pushes as one identity.
- VISTAGuard.

## Impact

- Backend: `ProjectTable.forum_repo_token` (an encrypted column, added to existing databases on
  start), `ForumSettings.remote_token`, `forum_git._token_config`,
  `services/debate.project_thread`, `api/debate._maybe_refresh`.
- UI: the project dialog (`ui/app/projects/page.tsx`), `ui/lib/forum-token-help.ts`.
- Docs: `docs/hypothesis-forum-hosting.md`.
