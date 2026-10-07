## Purpose

Define the lifetime of a chat turn independent of any page watching it, so a researcher can
leave a conversation mid-run and come back to its answer, its pending prompts, or an honest
record of how it ended.

## ADDED Requirements

### Requirement: A run outlives the page that started it

A chat turn SHALL keep running when every client watching it disconnects. Leaving the chat
page, switching conversations, or closing or reloading a browser tab SHALL NOT cancel the turn.
The turn's result SHALL be persisted to the conversation by the backend, whether or not anyone
is watching when it completes.

#### Scenario: Researcher navigates away mid-run

- **WHEN** a turn is running and the page streaming it disconnects
- **THEN** the turn runs to completion
- **AND** its result is saved to the conversation's model history

#### Scenario: Quitting VISTA ends runs

- **WHEN** VISTA quits while a turn is running
- **THEN** the turn ends with VISTA
- **AND** on the next launch the conversation shows that turn as interrupted

### Requirement: Re-attaching to a run

A client SHALL be able to watch a conversation's active run at any time. Re-attaching SHALL
replay every event of the run from its start, then follow live events until the run ends.
Several clients MAY watch the same run at once and SHALL each receive every event.

#### Scenario: Returning to a running conversation

- **WHEN** a researcher opens a conversation whose turn is still running
- **THEN** the page shows the steps taken so far and the live working status
- **AND** continues to update as the run proceeds

#### Scenario: Two views of one run

- **WHEN** the VISTA window and a browser tab both show the same running conversation
- **THEN** both receive the run's events

### Requirement: One active run per conversation

A conversation SHALL have at most one active run. Starting a turn in a conversation that
already has an active run SHALL be refused without affecting the running turn. Different
conversations SHALL be able to run at the same time.

#### Scenario: Send while busy

- **WHEN** a client starts a turn in a conversation whose run is still active
- **THEN** the request is refused with a conflict error
- **AND** the running turn and its eventual saved result are unchanged

#### Scenario: Busy conversation disables input

- **WHEN** the open conversation has an active run
- **THEN** the message input is disabled and a Stop control is shown

#### Scenario: Parallel conversations

- **WHEN** a researcher starts a turn in conversation B while conversation A is running
- **THEN** both turns run and each is saved to its own conversation

### Requirement: Stop keeps what happened

Only an explicit Stop SHALL cancel a running turn. A stopped turn SHALL remain in the
transcript marked "Stopped", and the model history SHALL keep the researcher's prompt and every
tool call that completed before the stop, so the agent's next turn knows what was already done.
A turn interrupted by VISTA quitting SHALL be recorded the same way as far as it was saved.

#### Scenario: Stop after a job was submitted

- **WHEN** a researcher stops a turn after its job-submission tool call completed
- **THEN** the transcript shows the turn as "Stopped"
- **AND** the next turn's model history contains that tool call and its result

#### Scenario: Stop releases a waiting prompt

- **WHEN** a researcher stops a turn that is waiting on a prompt
- **THEN** the prompt is withdrawn and the turn ends as stopped

### Requirement: Prompts wait for the researcher

Tool approvals and MCP form and URL elicitations, including the SSH login requested by Lux job
submission, SHALL wait until they are answered or the run is stopped. They SHALL NOT time out
while VISTA is running, within a 24-hour ceiling on any single MCP tool call. A client that
re-attaches SHALL be shown every prompt that is still unanswered, and SHALL NOT be shown prompts
that were already answered. Answers to prompts SHALL NOT be stored with the run's events.

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

### Requirement: A turn nobody watched is drawn like a live one

When a conversation is opened after a turn ran unwatched, the transcript SHALL show that turn
as it would have appeared live: the answer, the steps in the Activity tab, and the logs. The
events kept for this purpose SHALL be deleted once the conversation's transcript has been saved.

#### Scenario: Answer waiting on return

- **WHEN** a turn completed while the researcher was on another page
- **THEN** opening the conversation shows the question, the answer and the steps taken

#### Scenario: Stored events are cleaned up

- **WHEN** the page has drawn and saved an unwatched turn
- **THEN** the backend no longer holds that turn's events

### Requirement: Conversation run status

The conversation list SHALL show each conversation's run status with a distinct look:
amber and glowing while working; amber with a ring while a prompt needs the researcher;
green when a turn finished and has not been seen; red when a turn failed or was interrupted and
has not been seen. Green and red SHALL clear when the conversation is opened; "needs you" SHALL
clear when the prompt is answered. The unseen state SHALL survive a restart of VISTA.

#### Scenario: Finished while away

- **WHEN** a turn completes while its conversation is not open
- **THEN** the conversation shows the green dot until it is opened

#### Scenario: Waiting on a Lux login

- **WHEN** a running turn is waiting on an SSH login form
- **THEN** its conversation shows the "needs you" look instead of "working"

#### Scenario: Unseen survives restart

- **WHEN** VISTA restarts before a green or red conversation was opened
- **THEN** the dot is still shown after the restart

### Requirement: Nav rail summary

The nav rail's Chat entry SHALL show one summary dot for the most urgent status across the
project's conversations, in the order: needs you, failed or interrupted, done, then working. When exactly one
conversation needs the researcher, activating the Chat entry SHALL open that conversation;
otherwise it SHALL open the conversation list. VISTA SHALL NOT navigate on its own when a run's
status changes.

#### Scenario: Prompt while on another page

- **WHEN** a researcher is on the Skills page and a run starts waiting on an approval
- **THEN** the Chat entry shows the "needs you" dot and the Skills page stays open

#### Scenario: Jump to the waiting conversation

- **WHEN** exactly one conversation needs the researcher and they activate the Chat entry
- **THEN** that conversation opens and its prompt is shown
