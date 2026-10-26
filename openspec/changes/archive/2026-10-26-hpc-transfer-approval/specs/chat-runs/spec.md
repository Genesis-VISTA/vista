## ADDED Requirements

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

- **WHEN** a Lux submission asks for an SSH login
- **THEN** the prompt shows its fields with Submit, Decline and Cancel, as before
