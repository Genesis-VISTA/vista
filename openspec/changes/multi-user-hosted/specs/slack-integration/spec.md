## ADDED Requirements

### Requirement: One Slack app per hosted server

On a hosted deployment the Slack app's tokens and allowlists SHALL be server settings, set by
the administrator, and researchers SHALL NOT see or change them. Each Slack link SHALL belong
to the signed-in user who entered its code, and every turn, confirmation, limit, job
notification and credential reminder for that Slack user SHALL act as, and go to, that user
only.

#### Scenario: Two linked researchers

- **GIVEN** Alice and Bob each linked their Slack user on one hosted deployment
- **WHEN** Bob asks the bot about his jobs
- **THEN** the reply covers Bob's jobs only, and uses Bob's credentials

#### Scenario: Linking on hosted

- **WHEN** a Slack user DMs the bot `link` and Alice enters the code in the web app's Slack settings
- **THEN** the Slack user is linked to Alice, not to any other user
