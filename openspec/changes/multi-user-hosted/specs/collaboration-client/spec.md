## ADDED Requirements

### Requirement: Hosted signs in to the collaboration server automatically

On a hosted deployment the backend SHALL call the collaboration server with the access token
of the request it is serving, so a signed-in researcher is signed in to the collaboration
server with no token to paste. Settings SHALL NOT offer the collaboration token field when
hosted. Collaboration actions outside a request (such as a Slack turn) SHALL report that they
need the web app, not fail silently. A desktop install keeps today's sign-in.

#### Scenario: Publishing from the web app

- **GIVEN** a researcher signed in to the hosted deployment with the `apisix/vista` role
- **WHEN** they publish a skill
- **THEN** the collaboration server records them as its author, and they never pasted a token

#### Scenario: Collaboration status when hosted

- **WHEN** a signed-in researcher on a hosted deployment opens the Skill Hub
- **THEN** collaboration status reports signed in
