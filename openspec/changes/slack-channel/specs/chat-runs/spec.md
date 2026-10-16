## MODIFIED Requirements

### Requirement: Prompts wait for the researcher

Tool approvals and MCP form and URL elicitations, including the SSH login requested by Lux job
submission, SHALL wait until they are answered or the run is stopped. They SHALL NOT time out
while VISTA is running, within a 24-hour ceiling on any single MCP tool call. A client that
re-attaches SHALL be shown every prompt that is still unanswered, and SHALL NOT be shown prompts
that were already answered. Answers to prompts SHALL NOT be stored with the run's events.
In a run started from Slack, a tool approval SHALL be declined when it is unanswered after 10
minutes, and a form or URL elicitation SHALL be declined at once without being shown in Slack.

#### Scenario: Lux login after a long absence

- **WHEN** a Lux submission asks for an SSH login while the researcher is on another page, and
  they return an hour later
- **THEN** the login form is shown when they open the conversation
- **AND** answering it lets the submission proceed

#### Scenario: Answered prompt is not replayed

- **WHEN** a client re-attaches to a run whose earlier approval was already answered
- **THEN** that approval is not shown again

#### Scenario: Answer in one view clears the other

- **WHEN** two views show the same pending prompt and the researcher answers it in one
- **THEN** the other view stops showing it


#### Scenario: Approval in a Slack run expires

- **WHEN** a run started from Slack asks for a tool approval and nobody answers for 10 minutes
- **THEN** the tool call is declined and the run continues to its answer

#### Scenario: Login form in a Slack run

- **WHEN** a run started from Slack reaches a tool that asks for a Lux login
- **THEN** the form is declined immediately and the run's answer says to log in from the desktop
  app
