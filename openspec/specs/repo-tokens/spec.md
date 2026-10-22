# repo-tokens Specification

## Purpose
Where a GitHub or gitlab.com token comes from (the user's Settings → Repositories, or a
project's own forum token), and where it may be sent: skill import, the agent's
`fetch_repository` and a project's Hypothesis Lab forum.

## Requirements

### Requirement: Repository tokens live in Settings

A user SHALL be able to save a GitHub token and a gitlab.com token in the
Settings window. Each SHALL be stored encrypted and MUST NOT be returned by any
API response; the Settings window SHALL show only whether each is saved, and
SHALL let the user replace or remove it. Saving other settings SHALL keep both.
VISTA SHALL read repository tokens from nowhere else: no environment variable,
`.env` setting or project SHALL supply one.

#### Scenario: A saved token is never shown again

- **WHEN** a user saves a GitHub token in Settings and reopens Settings
- **THEN** it SHALL show that a GitHub token is saved, and SHALL NOT show the token

#### Scenario: Saving other settings keeps the tokens

- **GIVEN** a user with both tokens saved
- **WHEN** they change their research profile and save
- **THEN** both tokens SHALL still be saved

#### Scenario: Settings explains which token wins

- **WHEN** a user opens the Repositories section of Settings
- **THEN** it SHALL say that skill import, repository fetches and a Hypothesis Lab forum on
  the token's host use this Settings token, and that a forum elsewhere uses the system git's
  credentials

#### Scenario: The environment supplies nothing

- **GIVEN** `VISTA_BACKEND_GITHUB_TOKEN` set in the backend's environment and no token in Settings
- **WHEN** the user imports a skill from a private GitHub repository
- **THEN** no token SHALL be sent

### Requirement: Each token goes to its own host

Importing a skill from GitHub, and the agent's `fetch_repository`, SHALL send the
user's Settings token for the URL's host — the GitHub token to the GitHub API,
the gitlab.com token to the gitlab.com API — and SHALL send none to any other
host. A token SHALL NOT reach the code-execution sandbox. Without a token for the
host, only public repositories SHALL be reachable, and a refusal SHALL say to add
the token in Settings.

#### Scenario: Importing from a private GitHub repository

- **GIVEN** a GitHub token in Settings that can read `<owner>/<repo>`
- **WHEN** the user imports a skill from `https://github.com/<owner>/<repo>`
- **THEN** the import SHALL succeed

#### Scenario: A private repository without a token

- **GIVEN** no GitHub token in Settings
- **WHEN** the user imports a skill from a private GitHub repository
- **THEN** the import SHALL fail with a message saying to add a GitHub token in Settings

#### Scenario: Fetching from gitlab.com

- **GIVEN** both tokens in Settings
- **WHEN** the agent fetches a gitlab.com repository
- **THEN** only the gitlab.com token SHALL be sent, and only to gitlab.com

#### Scenario: A project's forum token is not borrowed

- **GIVEN** a project with an `https://github.com/` forum, and no GitHub token in Settings
- **WHEN** the agent in that project fetches a private GitHub repository
- **THEN** no token SHALL be sent, since a project holds none
