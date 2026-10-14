## MODIFIED Requirements

### Requirement: The project's forum remote is verified when saved

Saving a project with a forum repository SHALL initialise that project's working
repository and reach the remote before the save succeeds; an unreachable or
unauthorised remote SHALL fail the save with git's error. Pushes and fetches
to an `https://` remote SHALL use the project's access token when it has one,
else the user's Settings token for the remote's host (github.com or gitlab.com),
and the user's own git credentials otherwise; git MUST NOT prompt interactively.
Pointing a project at a repository that already holds threads SHALL join them.

#### Scenario: A typo in the repository URL

- **WHEN** a user saves a project with a forum URL that does not resolve
- **THEN** the save SHALL fail and show git's error in the dialog

#### Scenario: Joining an existing forum

- **GIVEN** a forum repository that already holds threads from another install
- **WHEN** a user saves a project pointing at it
- **THEN** those threads SHALL be listed for the project

#### Scenario: A token that cannot push

- **GIVEN** a private `https://` forum repository
- **WHEN** a user saves the project with a token the forge refuses
- **THEN** the save SHALL fail and show git's error in the dialog

#### Scenario: The Settings token serves a project without its own

- **GIVEN** a user with a GitHub token in Settings, and a project with no token of its own
- **WHEN** they save the project with a private `https://github.com/` forum repository the token can write to
- **THEN** the save SHALL succeed and debates SHALL publish to it

#### Scenario: The project's token wins

- **GIVEN** a user with a GitHub token in Settings, and a project with its own token
- **WHEN** git fetches or pushes that project's `https://github.com/` forum
- **THEN** git SHALL send the project's token, not the Settings one

### Requirement: A project may hold an access token for its forum repository

A project MAY store an access token for its forum repository. The token SHALL be
stored encrypted and MUST NOT be returned by any API response or carried by the
project model an agent is built from; responses SHALL say only whether one is
stored. A save that leaves the token out SHALL keep the stored one, and an empty
token SHALL remove it. Git SHALL receive the forum's token, whether the project's
or the user's Settings token, only on commands that reach the remote, only for an
`https://` remote, and scoped to that remote's URL. It MUST NOT appear in a
command's arguments or in the repository's configuration. A project's token
SHALL be used for its forum only, never for importing skills or fetching
repositories.

#### Scenario: The token is never read back

- **WHEN** a user saves a project with a forum token
- **THEN** the response SHALL say a token is stored and SHALL NOT contain it
- **AND** the database SHALL hold it encrypted

#### Scenario: Editing a project keeps its token

- **GIVEN** a project with a stored forum token
- **WHEN** a user saves the project without entering a token
- **THEN** the stored token SHALL be kept

#### Scenario: The token goes only to the forum repository

- **GIVEN** a project with a token and an `https://` forum URL
- **WHEN** git fetches or pushes the forum
- **THEN** git SHALL send the token to that URL and to no other
- **AND** a forum URL over plain `http://` or SSH SHALL receive no token

#### Scenario: The dialog says which token applies

- **GIVEN** a user with a GitHub token in Settings
- **WHEN** they enter an `https://github.com/` forum URL with no project token
- **THEN** the dialog SHALL say the Settings token will be used
