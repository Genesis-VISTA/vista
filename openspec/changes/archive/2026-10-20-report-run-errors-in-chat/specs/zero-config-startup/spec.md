## MODIFIED Requirements

### Requirement: Missing inference credentials are a reported state

An absent or rejected inference credential SHALL be reported as a named condition
directing the researcher to where it is configured. In chat, that report SHALL appear in the
conversation itself, not only in logs. It SHALL NOT crash a service, and SHALL NOT prevent any
feature that does not require inference from working.

#### Scenario: Chat attempted with no credential

- **WHEN** a researcher sends a message before configuring a credential
- **THEN** the conversation states that an inference credential is required and where to
  enter it, rather than surfacing an internal error or nothing at all
- **AND** the researcher's unsent text is back in the message input

#### Scenario: Chat with a rejected credential

- **WHEN** a researcher sends a message and the provider refuses the configured credential
- **THEN** the turn ends as failed, with a note stating that the key was rejected and where to
  change it

#### Scenario: Non-inference features remain available

- **WHEN** no inference credential is configured
- **THEN** projects, skills, and knowledge bases remain browsable
