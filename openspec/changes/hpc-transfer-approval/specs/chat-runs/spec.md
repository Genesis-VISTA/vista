## ADDED Requirements

### Requirement: A prompt with nothing to fill in is a yes-or-no question

An MCP form elicitation that asks for no fields SHALL be shown as its message with two answers,
Approve and Decline. Approve SHALL accept it and Decline SHALL decline it. Closing the prompt
without answering SHALL dismiss it, as it does any prompt. An elicitation that asks for fields
SHALL be shown as before.

#### Scenario: A transfer approval

- **WHEN** a tool asks the researcher to approve an upload, with no fields to fill in
- **THEN** the prompt shows the tool's message with Approve and Decline, and no Submit or Cancel

#### Scenario: A login form is unchanged

- **WHEN** a Lux submission asks for an SSH login
- **THEN** the prompt shows its fields with Submit, Decline and Cancel, as before
