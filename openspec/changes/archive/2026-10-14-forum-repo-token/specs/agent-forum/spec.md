## MODIFIED Requirements

### Requirement: The project's forum remote is verified when saved

Saving a project with a forum repository SHALL initialise that project's working
repository and reach the remote before the save succeeds; an unreachable or
unauthorised remote SHALL fail the save with git's error. Pushes and fetches
SHALL use the project's access token when it has one for an `https://` remote,
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

## ADDED Requirements

### Requirement: A project may hold an access token for its forum repository

A project MAY store an access token for its forum repository. The token SHALL be
stored encrypted and MUST NOT be returned by any API response or carried by the
project model an agent is built from; responses SHALL say only whether one is
stored. A save that leaves the token out SHALL keep the stored one, and an empty
token SHALL remove it. Git SHALL receive the token only on commands that reach
the remote, only for an `https://` remote, and scoped to that remote's URL. It
MUST NOT appear in a command's arguments or in the repository's configuration.

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

### Requirement: The project dialog helps make a forum token

For an `https://` forum URL on GitHub or a GitLab server, the project dialog SHALL
offer a link to that forge's token form, pre-filled for the repository with the
permissions the forum needs, and the steps the form cannot pre-fill. For any
other host it SHALL name those permissions instead.

#### Scenario: A GitHub repository

- **WHEN** a user enters a `https://github.com/<owner>/<repo>` forum URL and opens "Don't have a token?"
- **THEN** the dialog SHALL link to GitHub's fine-grained token form for `<owner>` with Contents set to read and write
- **AND** SHALL say to grant access to `<repo>` only

### Requirement: Viewing a debate survives a concurrent projection

Copying a thread into the database SHALL treat a post another writer stored
first as already present, not as an error. A refresh that fails while a debate
is being read SHALL be logged and SHALL NOT fail the read.

#### Scenario: Two writers project the same post

- **GIVEN** the debate task and a viewer's refresh both copying a new debate's thread
- **WHEN** one stores a post between the other's read and its insert
- **THEN** both SHALL succeed and the post SHALL be stored once

#### Scenario: A refresh fails

- **WHEN** the forum read behind a debate's refresh fails
- **THEN** reading the debate SHALL still return it
