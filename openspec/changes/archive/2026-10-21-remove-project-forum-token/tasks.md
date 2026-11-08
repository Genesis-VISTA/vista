## 1. Backend

- [x] 1.1 Remove `forum_repo_token` from `ProjectTable` and `ProjectCreate`, `has_forum_token` from `ProjectPublic` and `ProjectTable` (`db/schemas.py`), and its handling in `services/project.py`; verify the project API tests in `test_project_forum.py` pass without them
- [x] 1.2 Drop `repo_token`'s project argument and the `project` token source (`services/repo_tokens.py`), its use in `agents/forum/project_forum.py`, the `project` credential in `api/debate.py`'s `PublishError`, and the comment in `config.py`; verify `test_repo_tokens.py` and `test_project_forum.py` cover Settings token → system git, and that an old failure record naming `project` is not shown
- [x] 1.3 Update `test_skill_import.py` so it no longer builds a project with a token
- [x] 1.4 Add `project.forum_repo_token` to `RETIRED` in `scripts/migrate_columns.py`, so the reconcile drops the column and the encrypted token it may still hold; verify against a copy of a database that has the column

## 2. UI

- [x] 2.1 Remove the token field, "Remove the saved token" and "Don't have a token?" from the project dialog (`app/projects/page.tsx`), keeping a note of which credential the forum uses; remove `hasForumToken`/`forumRepoToken` from `lib/projects.ts` and `lib/agent-events.ts`
- [x] 2.2 Reduce `lib/forum-token-help.ts` to `forumTokenFallback` and rename it `lib/forum-credential.ts`; update `tests/forum-token-help.test.ts` accordingly
- [x] 2.3 Drop the `project` credential and the "give this project its own token" fixes from `lib/debates.ts`, and the project token from Settings → Repositories' explanation (`components/UserSettingsModal.tsx`); verify by the existing UI tests

## 3. Docs and verification

- [x] 3.1 Update `docs/hypothesis-forum-hosting.md`: github.com and gitlab.com use the Settings token, other hosts the system git (SSH key or credential helper)
- [x] 3.2 Run `./scripts/ci-local.sh backend ui` (lint and test) and verify it passes
