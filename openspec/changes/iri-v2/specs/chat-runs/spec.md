# Spec Delta

## MODIFIED Requirements

### Requirement: Prompts wait for the researcher

Tool approvals and MCP form and URL elicitations, such as a transfer approval or a login
form an MCP tool asks for, SHALL wait until they are answered or the run is stopped. They
SHALL NOT time out while VISTA is running, within a 24-hour ceiling on any single MCP tool
call. A client that re-attaches SHALL be shown every prompt that is still unanswered, and
SHALL NOT be shown prompts that were already answered. Answers to prompts SHALL NOT be
stored with the run's events. No Lux job tool SHALL ask for a login: Lux jobs use the
researcher's saved S3M token and Globus connection.

#### Scenario: A form after a long absence

- **WHEN** an MCP tool asks for a form while the researcher is on another page, and
  they return an hour later
- **THEN** the form is shown when they open the conversation
- **AND** answering it lets the tool call proceed

#### Scenario: Lux login after a long absence

- **WHEN** an agent submits a Lux job while the researcher is on another page, and they
  return an hour later
- **THEN** no login form is waiting for them, and the submission went ahead with their saved
  Lux S3M token and Globus connection

#### Scenario: Answered prompt is not replayed

- **WHEN** a client re-attaches to a run whose earlier approval was already answered
- **THEN** that approval is not shown again

#### Scenario: Answer in one view clears the other

- **WHEN** two views show the same pending prompt and the researcher answers it in one
- **THEN** the other view stops showing it

### Requirement: A prompt offering one choice is shown as buttons

An MCP form elicitation whose only field is a required choice among a few fixed answers SHALL be
shown as its message with one button per answer, labelled with that answer, and no form, Submit,
Decline or Cancel. A button SHALL accept the prompt with its answer. Closing the prompt without
answering SHALL dismiss it, as it does any prompt. Any other elicitation SHALL be shown as before.

#### Scenario: A transfer approval

- **WHEN** a tool asks the researcher to approve an upload, offering the answers Approve and Decline
- **THEN** the prompt shows the tool's message with an Approve and a Decline button, and nothing else
- **AND** pressing Decline accepts the prompt with the answer Decline

#### Scenario: A login form is unchanged

- **WHEN** an MCP tool asks for a form with text fields, such as a login
- **THEN** the prompt shows its fields with Submit, Decline and Cancel, as before
