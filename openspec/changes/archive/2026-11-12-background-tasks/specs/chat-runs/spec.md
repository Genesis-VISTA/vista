## REMOVED Requirements

### Requirement: One active run per conversation

**Reason**: A message sent to a busy conversation is now queued for the running turn, so the
busy refusal, the page's handling of it, and the disabled message box no longer exist.

**Migration**: Replaced by "At most one run per conversation" below, which keeps the limit of
one run and the parallel-conversations behaviour. Clients that handled the busy conflict on a
send now receive the queued message instead; queueing is defined by the conversation-inbox
capability.

## ADDED Requirements

### Requirement: At most one run per conversation

A conversation SHALL have at most one active run. A message sent to a conversation that
already has an active run SHALL NOT start a second run and SHALL NOT be refused: it SHALL be
queued for the running turn as the conversation-inbox capability defines, without otherwise
affecting that turn. Any refusal of a send SHALL be reported with its own reason. Different
conversations SHALL be able to run at the same time.

#### Scenario: Send while busy

- **WHEN** a client sends a message to a conversation whose run is still active
- **THEN** the request is accepted and the message is queued
- **AND** no second run starts

#### Scenario: Refusals are shown

- **WHEN** the chat page's send is refused for any reason
- **THEN** the page shows the refusal's message in the conversation and puts the unsent text
  back in the input

#### Scenario: Busy conversation keeps its input

- **WHEN** the open conversation has an active run
- **THEN** the message input stays enabled and a Stop control is shown

#### Scenario: Parallel conversations

- **WHEN** a researcher starts a turn in conversation B while conversation A is running
- **THEN** both turns run and each is saved to its own conversation
