## Why

A project row holds one secret: `forum_repo_token`, an optional access token for its
Hypothesis Lab forum. The collaboration server (see `PLAN.md`, Step 5) will publish projects,
and a project that carries a credential cannot be published as-is. The token is also the
only way a forum on a host other than github.com or gitlab.com gets a token, and that case
is already served by the system git: an SSH key or a credential helper.

## What Changes

- **BREAKING**: a project no longer stores a forum token. `ProjectTable.forum_repo_token`,
  `ProjectCreate.forum_repo_token` and `ProjectPublic.has_forum_token` are removed, and a
  stored token is no longer read. `scripts/migrate_columns.py` drops the column from an existing database.
- A forum's credential is the user's Settings token for the forum's host (github.com or
  gitlab.com), else the system git's credentials. `repo_token` loses its project argument.
- The project dialog loses the token field and "Don't have a token?". It says which
  credential the forum will use, and that hosts other than github.com and gitlab.com use the
  system git.
- A failed publish can no longer name "this project's token"; its fixes point at
  Settings → Repositories or the system git only.
- Settings → Repositories and `docs/hypothesis-forum-hosting.md` describe the two-step order.

## Capabilities

### Modified Capabilities

- `agent-forum`: the save-time check and the failed-publish requirement lose the project
  token; the project-token and token-help requirements are removed.
- `repo-tokens`: the purpose and "which token wins" no longer mention a project token; the
  "not borrowed" scenario goes.

## Impact

- Backend: `db/schemas.py`, `services/project.py`, `services/repo_tokens.py`,
  `agents/forum/project_forum.py`, `api/debate.py`, `config.py`.
- UI: `app/projects/page.tsx`, `lib/projects.ts`, `lib/agent-events.ts`, `lib/debates.ts`,
  `lib/forum-token-help.ts`, `components/UserSettingsModal.tsx`.
- Tests: `test_project_forum.py`, `test_repo_tokens.py`, `test_skill_import.py`, UI tests
  that build a `Project`.

## Non-goals

- Moving the Hypothesis Lab onto the collaboration server, or any other forum change.
- Tokens for hosts other than github.com and gitlab.com.
- A data migration: the column is left in existing databases, and no token is carried over.
- VISTAGuard.
