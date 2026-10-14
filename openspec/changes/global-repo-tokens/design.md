## Context

Three callers send a repository token today, from two places:

| Caller | Where | Token today |
| --- | --- | --- |
| Skill import (`POST /skills/import`) | `agents/skill_import._api_request` | `settings.github_token` |
| Agent's `fetch_repository` | `agents/source_fetch._token` | `settings.github_token` / `gitlab_token` |
| Project forum fetch / push | `agents/forum/project_forum.forum_config_for` | `ProjectTable.forum_repo_token` (from `forum-repo-token`) |

The user's other credentials (model provider keys, S3M, Globus) are `EncryptedStr` columns on
`UserTable`, edited through `UserSelfUpdate` and returned in plain text by
`UserPublicWithConfig`, which is also what an agent is built from. `forum_config_for(project)`
takes no user; it has 14 call sites, nearly all in routes that already have `UserDep`.

## Goals / Non-Goals

**Goals:**
- One function decides which token a repository URL gets, so the three callers cannot drift.
- Tokens are write-only, like the project's forum token, and never reach a model or the
  sandbox.

**Non-Goals:**
- Hosts other than github.com and gitlab.com (see proposal).
- Changing what `fetch_repository` is allowed to read.

## Decisions

**D1. One resolver, `services/repo_tokens.repo_token(url, user, project=None)`.**
It returns the token for a URL or None. With `project` given (only the forum passes one), the
project's token wins; otherwise the user's token for the URL's host. Host comes from the
parsed URL: `github.com` and `api.github.com` are GitHub, `gitlab.com` is gitlab.com, anything
else gets None. Imports and fetches never pass a project, which is what keeps a repo-scoped
forum token out of them. *Alternative:* a `purpose` argument (`"forum"` / `"read"`); rejected
because "a project is passed" already says it, and a second way to say it can disagree.

**D2. The resolver reads the user's row, not the agent's cached user.**
The tokens are left out of `UserPublicWithConfig`, so an agent's model never holds them.
Callers pass the user id and the resolver loads `UserTable` at call time. This also means a
changed token applies on the next call with no agent eviction. If `fetch_repository`'s tool
context has no session, task 3.2 opens one from the session factory.

**D3. Write-only fields, with their own null handling.**
`UserSelfUpdate.github_token` / `gitlab_token`: absent or None keeps, `""` removes, a value
replaces. They must NOT go into `_USER_CONFIG_NULLABLE_FIELDS`, which turns `""` into None and
would make "remove" mean "keep". The read model gets `has_github_token` / `has_gitlab_token`.

**D4. The forum takes the user.**
`forum_config_for(project, user)`, and likewise `build_client_for`, `lab_enabled` and
`ensure_forum`. Background debate and campaign tasks pass the run's user, which the
orchestrator is already built with. `remote_token` on `ForumSettings` is unchanged: it holds
whichever token the resolver picked.

**D5. The environment settings go, out loud.**
`Settings.github_token` and `gitlab_token` are deleted. Settings uses `extra="ignore"`, so a
leftover `.env` line would be silently ignored; instead the backend logs one warning at start
naming the variable and pointing at Settings, as `check_legacy_forum_env` does for the old forum
setting.

**D6. The Settings window gets a "Repositories" section** after "Model provider": two password
fields with the same saved / replace / remove behaviour as the project dialog, each with a
pre-filled "Create a token" link. GitHub's asks for Contents read-only on all repositories (or
read and write for a forum to fall back to it); gitlab.com's for `read_api`,
`read_repository` and `write_repository`. The project dialog's token field shows "Your GitHub
token from Settings will be used" when the forum is on that host and no project token is set.

## Risks / Trade-offs

- **A Settings token used for a forum needs write access**, broader than imports need. The
  Settings link offers the read-only and read-write forms separately, and the hint in the
  project dialog makes the fallback visible.
- **Dev `.env` files stop supplying tokens.** Mitigated by D5's warning; desktop packages
  never read them.
- **`fetch_repository` reads whatever the user's token can.** Same as today with a deployment
  token, but now it is the researcher's own reach, which is the intent of #75.
