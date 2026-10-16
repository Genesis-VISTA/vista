Written after the work, on `debate-fixes` (!159). Every task was done there, all hermetic.

## 1. A debate's first view survives the projection race

- [x] 1.1 `project_thread` inserts new posts with `ON CONFLICT DO NOTHING` and returns only the
  posts it inserted (`backend/tests/test_debate_service.py`:
  `test_a_post_another_session_projected_first_is_not_a_conflict`).
- [x] 1.2 `_maybe_refresh` reports a rollback as an ended transaction, so `get_debate` re-reads
  the run (`backend/tests/test_debate_api.py`:
  `test_a_failed_refresh_still_returns_the_debate`).

## 2. A project's forum access token

- [x] 2.1 `ProjectTable.forum_repo_token`, encrypted; `ProjectCreate.forum_repo_token` is
  write-only (absent keeps, `""` removes); `ProjectPublic.has_forum_token`.
- [x] 2.2 `forum_config_for` carries the token as `ForumSettings.remote_token`.
- [x] 2.3 Network git commands get an `http.<remote>.extraHeader` through `GIT_CONFIG_*`, only
  for an `https://` remote; local plumbing never does
  (`backend/tests/test_project_forum.py`, the "forum access token" section, which asks git
  itself with `git config --get-urlmatch`).
- [x] 2.4 The project dialog's token field, shown for an `https://` URL, with "Remove the saved
  token".
- [x] 2.5 `docs/hypothesis-forum-hosting.md` describes the token and its permissions.

## 3. "Don't have a token?"

- [x] 3.1 `forumTokenHelp` builds the pre-filled GitHub or GitLab token link and steps, or none
  for a host it cannot identify (`ui/tests/forum-token-help.test.ts`).
- [x] 3.2 The disclosure under the token field in the project dialog.

## 4. Tried live

- [x] 4.1 A private GitHub repository with a fine-grained token made from the pre-filled form:
  saving the project synced, and a debate published a `vista-forum/threads/…` branch with no
  refresh failures in `logs/backend.log`. Manual; not in PR CI.
