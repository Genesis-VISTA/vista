## ADDED Requirements

### Requirement: A failed turn says why

A turn that fails SHALL end with a note in the conversation that states the reason it failed,
not only that it failed. When the failure came from the model provider, the reason SHALL name
the model, the provider's HTTP status, and the provider's own message where it gave one, and
SHALL NOT be a raw dump of the provider's response. The reason SHALL be kept with the turn's
events, so a conversation opened after the turn ended shows the same note. A turn saved without
a reason SHALL still show that it failed.

#### Scenario: Unknown model name

- **WHEN** a researcher sends a message and the provider answers 404 because the chosen model
  does not exist
- **THEN** the turn ends with a note saying it failed, naming the model, the 404, and the
  provider's message

#### Scenario: Failure seen after returning

- **WHEN** a turn failed while the researcher was on another page
- **THEN** opening the conversation shows the same failure note with its reason

#### Scenario: Older failed turn

- **WHEN** a conversation is opened whose failed turn was saved without a reason
- **THEN** the turn ends with a note saying it failed

## MODIFIED Requirements

### Requirement: One active run per conversation

A conversation SHALL have at most one active run. Starting a turn in a conversation that
already has an active run SHALL be refused without affecting the running turn, and the refusal
SHALL identify itself as a busy conversation, distinct from any other refusal of the same
request. Different conversations SHALL be able to run at the same time.

#### Scenario: Send while busy

- **WHEN** a client starts a turn in a conversation whose run is still active
- **THEN** the request is refused with a conflict error identified as a busy conversation
- **AND** the running turn and its eventual saved result are unchanged

#### Scenario: Busy refusal shows the running turn

- **WHEN** the chat page's send is refused because the conversation is busy
- **THEN** the page shows the running turn and puts the unsent text back in the input

#### Scenario: Other refusals are not mistaken for busy

- **WHEN** the chat page's send is refused for any reason other than a busy conversation
- **THEN** the page shows the refusal's message in the conversation and puts the unsent text
  back in the input, and does not try to show a running turn

#### Scenario: Busy conversation disables input

- **WHEN** the open conversation has an active run
- **THEN** the message input is disabled and a Stop control is shown

#### Scenario: Parallel conversations

- **WHEN** a researcher starts a turn in conversation B while conversation A is running
- **THEN** both turns run and each is saved to its own conversation
