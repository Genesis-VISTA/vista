## Purpose

Define the one inbox each conversation has for everything addressed to its agent: the
researcher's messages and the results of background work. It decides whether an item joins
the running turn or starts a new one, and what holds a conversation back from starting turns
nobody asked for.

## ADDED Requirements

### Requirement: One inbox per conversation

Each conversation SHALL have one inbox that receives the researcher's messages and the
results of that conversation's background tasks. Items SHALL be delivered to the agent in the
order they arrived. With no turn running and nothing holding the conversation back, an
arriving item SHALL start a turn. With a turn running, an arriving item SHALL be delivered to
that turn at the agent's next step, without ending the turn.

#### Scenario: Message to an idle conversation

- **WHEN** a researcher sends a message to a conversation with no turn running
- **THEN** a turn starts with that message, as before

#### Scenario: Result during a turn

- **WHEN** a background command ends while its conversation's turn is running
- **THEN** the agent receives the result at its next step in the same turn

#### Scenario: Arrival order is kept

- **WHEN** a result and then a researcher message arrive during one long tool call
- **THEN** the agent receives the result before the message

### Requirement: Messages queue while the agent is busy

A researcher SHALL be able to send a message while the conversation's turn is running. The
message SHALL be shown as queued until the agent takes it, and SHALL then appear in the
conversation as a message from the researcher. Queued messages SHALL be the same in every
view of the conversation and SHALL survive a reload of the page.

#### Scenario: Sending during a turn

- **WHEN** a researcher sends a message while the agent is in a tool call
- **THEN** the message is shown as queued
- **AND** when that tool call ends, the agent receives the message and it appears in the
  conversation

#### Scenario: Queue seen from a second view

- **WHEN** a message is queued in the VISTA window while a browser tab shows the same
  conversation
- **THEN** the browser tab shows the queued message

### Requirement: A queued message can be changed until it is taken

A researcher SHALL be able to edit or delete a queued message until the agent has taken it.
An edit or delete that arrives after the message was taken SHALL be refused, and the view
SHALL then show the message as delivered.

#### Scenario: Deleting a queued message

- **WHEN** a researcher deletes a queued message before the agent's next step
- **THEN** the agent never receives it

#### Scenario: Editing a queued message

- **WHEN** a researcher edits a queued message before the agent's next step
- **THEN** the agent receives the edited text

#### Scenario: Too late to change

- **WHEN** a researcher edits a message the agent has already taken
- **THEN** the edit is refused and the message is shown as delivered, unchanged

### Requirement: A task result can start a turn

When a background task's result arrives at a conversation with no turn running, VISTA SHALL
start a turn with that result, without a message from the researcher. A self-started turn
SHALL be a turn in every other respect: it outlives the page, is saved to the conversation,
can be stopped, and shows the conversation's run status.

#### Scenario: Command ends while the researcher is away

- **WHEN** a background command ends in a conversation that is idle and not open
- **THEN** a turn starts with the result
- **AND** the conversation shows the "finished, not seen" look when the turn completes

### Requirement: A self-started turn shows what started it

A turn that VISTA started SHALL open, where a researcher's message would be, with a one-line
row naming the event that started it, such as a task finishing with its exit status and
runtime. The row SHALL expand to the full result the agent received and SHALL NOT look like a
message the researcher typed. A result delivered into a running turn SHALL be shown the same
way at the point it was delivered.

#### Scenario: Event row for a finished command

- **WHEN** a turn was started by a background command exiting with status 0 after 4 minutes
- **THEN** the conversation shows a one-line row saying the task finished, its status and its
  runtime, followed by the agent's reply
- **AND** expanding the row shows the result's full text

### Requirement: Self-started turns are capped

VISTA SHALL start at most 10 turns in a row in one conversation without a message from the
researcher. A result that arrives after the tenth SHALL NOT start a turn: it SHALL wait in the
inbox and the conversation SHALL show the "needs you" look. A message from the researcher
SHALL reset the count, and waiting results SHALL be delivered with that message.

#### Scenario: Eleventh result waits

- **WHEN** 10 self-started turns have run in a row and another result arrives
- **THEN** no turn starts and the conversation shows "needs you"

#### Scenario: Researcher returns

- **WHEN** the researcher then sends a message
- **THEN** the turn that starts includes the waiting result ahead of the message
- **AND** later results can start turns again

### Requirement: Stop quiets the conversation

Stopping a turn SHALL leave the conversation's background tasks running. Messages that were
queued and not yet taken SHALL be returned to the message box as unsent text. From the stop
until the researcher's next message, results SHALL collect in the inbox without starting a
turn, and a conversation with a collected result SHALL show the "needs you" look. The same
SHALL apply when a turn is interrupted by VISTA quitting.

#### Scenario: Result lands just after Stop

- **WHEN** a researcher stops a turn and a background command ends a second later
- **THEN** no turn starts
- **AND** the result is delivered with the researcher's next message

#### Scenario: Queued message at Stop

- **WHEN** a researcher stops a turn while one of their messages is still queued
- **THEN** the message's text is back in the message box and the agent has not received it

#### Scenario: Tasks keep running

- **WHEN** a researcher stops a turn while a background command runs
- **THEN** the command is still running afterwards

### Requirement: Waiting on the researcher is shown

A conversation SHALL show the "needs you" look while a result is waiting in its inbox for the
researcher's next message, or while one of its job watches is paused for lost cluster access.
The look SHALL clear when the waiting results have been delivered and no watch is paused. It
SHALL survive a restart of VISTA.

#### Scenario: Paused watch

- **WHEN** a conversation's job watch pauses because cluster access expired
- **THEN** the conversation shows "needs you" until the watch resumes or is stopped
