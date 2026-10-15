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
It returns a `RepoToken(value, source)` for a URL, where `source` is `"project"`,
`"settings-github"` or `"settings-gitlab"`, or None when no token applies (so git falls back
to the system's credentials, which D7 reports as `"system-git"`). The source is part of the
answer, not something a caller re-derives, because D7 has to name it. With `project` given (only the forum passes one), the
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
`read_repository` and `write_repository`. A short note at the top of the section gives the
order: a forum uses its project's token if it has one, else this token, else the system git's
credentials; imports and fetches always use this token. The project dialog's token field is
labelled as an override ("Use a different token for this forum"), says it is for the forum
only, and when empty says "Your GitHub token from Settings will be used" (or that the system
git's credentials will be, when there is none for the host).

**D7. A failed publish is recorded with its credential, beside the outbox.**
When a fetch or push fails, the forum client classifies git's error: refused (HTTP 401, 403,
or GitHub's 404 "Repository not found" for a repository the credential cannot see,
"Authentication failed", "Permission denied"), unreachable (DNS, connection refused, a
timeout), or `failed` (anything else, shown with git's message). It writes `{reason, credential, at}` to `data/forum-git/<project-id>/last-publish.json`,
where `credential` is the `RepoToken.source` it was built with or `"system-git"`, and deletes
the file on the next successful push. It lives there, not in `vista.db`, for the reason the
outbox does: the forum is written from inside open app-DB transactions. `GET
/forum/status` returns it as `publish_error`, and the lab's existing "not reached the forum
yet" notice names the credential and links to the project dialog or Settings. Git's message
can be shown as it is: the token reaches git only through the environment, so it is never in
an argument or an error.

## Risks / Trade-offs

- **A Settings token used for a forum needs write access**, broader than imports need. The
  Settings link offers the read-only and read-write forms separately, and the hint in the
  project dialog makes the fallback visible.
- **Dev `.env` files stop supplying tokens.** Mitigated by D5's warning; desktop packages
  never read them.
- **Classifying git's errors by text is brittle.** Forges word refusals differently. D7 keeps
  the classification in one function with a table test of real messages from GitHub and
  GitLab. Anything it doesn't recognise is reported as `failed` with git's message, naming no
  credential and not claiming the forum is unreachable, so a misread never blames the wrong
  token or hides a real refusal behind "unreachable".
- **`fetch_repository` reads whatever the user's token can.** Same as today with a deployment
  token, but now it is the researcher's own reach, which is the intent of #75.
