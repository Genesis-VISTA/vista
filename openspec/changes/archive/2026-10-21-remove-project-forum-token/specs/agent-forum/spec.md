## ADDED Requirements
### Requirement: The forum remote is verified when a project is saved

Saving a project with a forum repository SHALL initialise that project's working
repository and reach the remote before the save succeeds; an unreachable or
unauthorised remote SHALL fail the save with git's error. Pushes and fetches
to an `https://` remote SHALL use the user's Settings token for the remote's host
(github.com or gitlab.com), and the user's own git credentials otherwise; git
MUST NOT prompt interactively. A project SHALL NOT hold a token of its own.
Pointing a project at a repository that already holds threads SHALL join them.

#### Scenario: A typo in the repository URL

- **WHEN** a user saves a project with a forum URL that does not resolve
- **THEN** the save SHALL fail and show git's error in the dialog

#### Scenario: Joining an existing forum

- **GIVEN** a forum repository that already holds threads from another install
- **WHEN** a user saves a project pointing at it
- **THEN** those threads SHALL be listed for the project

#### Scenario: A token that cannot push

- **GIVEN** a private `https://github.com/` forum repository, and a GitHub token in Settings the forge refuses
- **WHEN** a user saves the project
- **THEN** the save SHALL fail and show git's error in the dialog

#### Scenario: The Settings token serves the forum

- **GIVEN** a user with a GitHub token in Settings
- **WHEN** they save the project with a private `https://github.com/` forum repository the token can write to
- **THEN** the save SHALL succeed and debates SHALL publish to it

#### Scenario: Another host uses the system git

- **GIVEN** a forum on an `https://` host other than github.com or gitlab.com
- **WHEN** git fetches or pushes that forum
- **THEN** git SHALL send no token, and the system git's credentials SHALL apply

### Requirement: A failed publish names the Settings token or the system git

When posts cannot be published to a project's forum, the Hypothesis Lab SHALL
say why, from the most recent failed attempt: either the remote refused the
credential, naming which one was sent (the Settings token for the host, or the
system git's credentials) and where to change it; or that the remote could not
be reached; or else git's message. A refused
credential MUST NOT be reported as the forum being unreachable. The reason SHALL
clear once a publish succeeds. The token itself MUST NOT appear in the message.

#### Scenario: The Settings token is refused

- **GIVEN** a GitHub Settings token that cannot push to the project's forum
- **WHEN** a debate's post fails to publish
- **THEN** the lab SHALL say the GitHub token from Settings was refused and point to Settings

#### Scenario: No token was sent

- **GIVEN** a forum reached with the system git's credentials
- **WHEN** a post fails to publish because the remote refused them
- **THEN** the lab SHALL say the system git's credentials were refused, and that a token can be added in Settings for github.com or gitlab.com, or an SSH key or credential helper set up for the system git

#### Scenario: The forum is unreachable

- **WHEN** a post fails to publish because the remote could not be reached
- **THEN** the lab SHALL say the forum could not be reached, and SHALL NOT name a token as the cause

#### Scenario: A later success clears it

- **GIVEN** a failure reason on show
- **WHEN** the next publish succeeds
- **THEN** the reason SHALL no longer be shown

#### Scenario: An old record naming the project's token

- **GIVEN** a stored failure that names the project's token as the credential sent
- **WHEN** the lab page loads
- **THEN** no failure reason SHALL be shown

### Requirement: Git receives the forum's token only on the way to its remote

Git SHALL receive the forum's Settings token only on commands that reach the
remote, only for an `https://` remote, and scoped to that remote's URL. It MUST
NOT appear in a command's arguments or in the repository's configuration. The
project dialog SHALL say which credential the forum will use: the Settings token
for its host when one is saved, else the system git's credentials.

#### Scenario: The token goes only to the forum repository

- **GIVEN** a GitHub token in Settings and an `https://github.com/` forum URL
- **WHEN** git fetches or pushes the forum
- **THEN** git SHALL send the token to that URL and to no other
- **AND** a forum URL over plain `http://` or SSH SHALL receive no token

#### Scenario: The dialog says which credential applies

- **GIVEN** a user with a GitHub token in Settings
- **WHEN** they enter an `https://github.com/` forum URL in the project dialog
- **THEN** the dialog SHALL say the Settings token will be used

#### Scenario: The dialog names the system git elsewhere

- **WHEN** a user enters an `https://` forum URL on a host other than github.com or gitlab.com, or an SSH URL on any host
- **THEN** the dialog SHALL say the system git's credentials will be used, such as an SSH key or a credential helper
- **AND** it SHALL offer no token field

#### Scenario: Settings not loaded yet

- **WHEN** the project dialog has not yet learned which tokens the user has saved in Settings
- **THEN** it SHALL name no credential, rather than one that may be wrong

## REMOVED Requirements

### Requirement: A project may hold an access token for its forum repository

**Reason**: A project row must hold no secret so it can be published to the collaboration
server as-is (`PLAN.md`, Step 1).
**Migration**: Save a GitHub or gitlab.com token in Settings → Repositories; for other hosts,
give the system git an SSH key or a credential helper. The token-scoping rules move to
"Git receives the forum's token only on the way to its remote".

### Requirement: The project dialog helps make a forum token

**Reason**: There is no project token left to make. Settings → Repositories links to each
forge's token form.
**Migration**: None.

### Requirement: The project's forum remote is verified when saved

**Reason**: Its credential order began with the project's own token, which projects no longer
hold.
**Migration**: Replaced by "The forum remote is verified when a project is saved", which is the
same check with the Settings token, then the system git.

### Requirement: A failed publish names the credential it used

**Reason**: One of the credentials it names, the project's token, no longer exists.
**Migration**: Replaced by "A failed publish names the Settings token or the system git".
