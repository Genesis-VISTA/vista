## Why

Importing a skill from a private GitHub repository, and the agent's `fetch_repository` on a
private GitHub or gitlab.com repository, read only `VISTA_BACKEND_GITHUB_TOKEN` and
`VISTA_BACKEND_GITLAB_TOKEN` from `.env`. An installed package never reads `.env`, so in the
desktop app neither can reach a private repository at all. That is the half of #75 that
`forum-repo-token` (!159) left open.

## What Changes

- The Settings window gains a **Repositories** section with two tokens: GitHub and gitlab.com.
  Each is stored encrypted on the user, never read back, and can be removed.
- One resolver picks the token for a URL, and every repository caller uses it:
  - **Skill import** and **`fetch_repository`**: the user's token for the URL's host.
  - **A project's forum**: the project's own token, else the user's token for the forum's host,
    else the system git's credentials.
  A project token never stands in for imports or fetches: it is usually scoped to the forum
  repository alone.
- **BREAKING** (dev checkouts only): `VISTA_BACKEND_GITHUB_TOKEN` and
  `VISTA_BACKEND_GITLAB_TOKEN` are removed. Settings is the only source.
- The Settings token is the default; a project's token is an optional override for that
  project's forum. The Settings section explains the order (project token, then Settings
  token, then the system git's credentials), and the project dialog's token field says it
  overrides the Settings token for this forum only, and which token applies when it is empty.
- When a debate's post cannot be published, the Hypothesis Lab says which credential was
  refused (this project's token, the Settings token for the host, or the system git's
  credentials) and where to change it, or that the forum could not be reached at all.
- `docs/skill-onboarding.md` and `docs/hypothesis-forum-hosting.md` point at Settings.

## Capabilities

### New Capabilities

- `repo-tokens`: where a repository token comes from, and where it may be sent, for skill
  import, `fetch_repository` and a project's forum.

### Modified Capabilities

- `agent-forum`: the save-time remote check and the project-token requirement gain the
  Settings token as the fallback before the system git's credentials; a new requirement that
  a failed publish names the credential it used.

The open change `hpc-skill-authoring` names the two environment settings in its
`fetch_repository` requirement. Its capability is not in the main specs yet, so this change
amends that sentence in its delta instead of carrying a delta of its own (task 6.1).

## Non-goals

- Tokens for any host other than github.com and gitlab.com, except through a project's forum
  token, which already covers any `https://` forum.
- A token per project for imports or fetches.
- Making the agent's reach narrower than the token's: `fetch_repository` still reads whatever
  the user's token can, now the user's own rather than a deployment's.
- VISTAGuard.

## Impact

- Backend: two `EncryptedStr` columns on `UserTable` (`github_token`, `gitlab_token`), the user
  update and read models, a new `services/repo_tokens.py`, `agents/skill_import.py`,
  `agents/source_fetch.py`, `agents/forum/project_forum.py` and its 14 call sites, which now pass
  the user. `Settings.github_token` and `Settings.gitlab_token` are deleted.
- UI: `ui/components/UserSettingsModal.tsx`, `ui/lib/user.ts`, the project dialog's hint.
- Docs: `docs/skill-onboarding.md`, `docs/hypothesis-forum-hosting.md`.
