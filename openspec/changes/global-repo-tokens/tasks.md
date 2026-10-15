Branch `global-repo-tokens`, on top of `debate-fixes` (!159). All tests hermetic unless marked.

## 1. Tokens on the user, and the resolver

- [x] 1.1 `UserTable.github_token` / `gitlab_token` as `EncryptedStr`; `UserSelfUpdate` fields
  that are write-only per design D3 (kept out of `_USER_CONFIG_NULLABLE_FIELDS`); read flags
  `has_github_token` / `has_gitlab_token`; neither token in `UserPublicWithConfig`. Verify with
  a new `backend/tests/test_repo_tokens.py`: save, read back only the flag, keep on an
  unrelated save, remove with `""`, stored encrypted.
- [x] 1.2 `services/repo_tokens.repo_token(url, user, project=None)` returning a
  `RepoToken(value, source)` or None, per D1–D2. **Sam writes this one.** Verify with a table
  test in the same file: github.com and api.github.com get the GitHub token (`settings-github`),
  gitlab.com the gitlab.com one (`settings-gitlab`), any other host none; with a project, its
  token wins (`project`) and the user's is the fallback; without a project, a project's token
  is never returned.

## 2. Settings window

- [x] 2.1 A "Repositories" section in `ui/components/UserSettingsModal.tsx` with the two
  fields, saved / replace / remove, pre-filled "Create a token" links, and the order-of-use
  note (D6); `ui/lib/user.ts` types. Verify with `ui/tests/UserSettingsModal.test.tsx`: the
  saved state shows no token, an unrelated save sends no token field, remove sends `""`, and
  the note names the project token, then Settings, then system git.

## 3. Callers use the resolver

- [x] 3.1 Skill import sends `repo_token(url, user)`; a 401/403 says to add a GitHub token in
  Settings. Verify in the skill-import tests with a stubbed API: the header is sent with a
  token, absent without one, and the message names Settings.
- [x] 3.2 `fetch_repository` sends `repo_token(url, user)`, opening a session if its tool
  context has none (D2). Verify in `backend/tests/test_source_fetch.py`: each token only to its
  own API host; a project's forum token never sent.
- [x] 3.3 `forum_config_for(project, user)` and its callers (D4), with the Settings token as
  the forum fallback. Verify in `backend/tests/test_project_forum.py`: the project's token wins,
  the Settings token serves a project without one, neither for a non-matching host; and the
  existing debate/API suites pass with the user threaded through.
- [x] 3.4 The project dialog's token field as an override (D6): its label and note (forum
  only, overrides Settings), and the hint for which token applies when it is empty. Verify with
  a UI test of the hint for each case: Settings token for the host, none (system git).

## 4. A failed publish names its credential

- [x] 4.1 Classify git's fetch/push errors as refused / unreachable / failed, and record
  `{reason, credential, at}` in `last-publish.json`, cleared on success (D7). Verify in
  `backend/tests/test_forum_git.py`: a table of real GitHub and GitLab refusal messages, a
  refusal against a bare remote made to reject the push (e.g. a `pre-receive` hook that exits
  1 and prints `Permission denied`), an unreachable remote, and the file gone after a success.
- [x] 4.2 `GET /forum/status` returns `publish_error`. Verify in
  `backend/tests/test_debate_api.py` with the fake forum reporting each case.
- [x] 4.3 The lab's "not reached the forum yet" notice names the credential and links to the
  project dialog or Settings, says "could not be reached" only for unreachable, and shows git's
  message for `failed`. Verify with a UI test per case.

## 5. Remove the environment settings

- [x] 5.1 Delete `Settings.github_token` / `gitlab_token`; log one warning at start when either
  variable is set (D5). Verify with a test that sets the variable and checks the warning, and
  `rg -n 'GITHUB_TOKEN|GITLAB_TOKEN' backend/src` finds only the warning.

## 6. Docs and the neighbouring change

- [x] 6.1 Archive `hpc-skill-authoring` (its work has shipped), then carry a `MODIFIED` delta of
  its `fetch_repository` requirement that sends "the user's Settings token for the host" instead
  of the two environment settings. Its archived design note stays as the record of the time.
  Verify with `openspec validate --all`.
- [x] 6.2 `docs/skill-onboarding.md` and `docs/hypothesis-forum-hosting.md` point at Settings.
  Verify with `./scripts/ci-local.sh docs`.

## 7. Verify

- [x] 7.1 `./scripts/ci-local.sh` passes in full before pushing.
- [ ] 7.2 Manual, not in PR CI: with only a Settings GitHub token, import a skill from a private
  repository, and a project with no token of its own publishes a debate to a private forum.
