# slack-integration Specification

## Purpose
Lets a researcher talk to their VISTA agent from Slack, on desktop or phone, with the same agent
and the same conversation inbox as the in-app chat, and with safeguards suited to a device that
can be lost.

## Requirements

### Requirement: Slack is off until enabled

Slack integration SHALL be off by default. While it is off, VISTA SHALL open no connection to
Slack and SHALL behave exactly as without it. Enabling it SHALL require a bot token and an
app-level token, both held in the operating system's secret store, or in a secrets directory
readable only by VISTA's user when VISTA runs hosted, and never in the database. Where neither is
available, enabling SHALL be refused with the reason.
The tokens SHALL NOT be returned by any API; the settings SHALL only report whether each is set.

#### Scenario: A default install

- **WHEN** VISTA starts with Slack never configured
- **THEN** no connection to Slack is opened and no Slack setting affects any other behaviour

#### Scenario: Tokens are not readable back

- **WHEN** the settings are read after both tokens were saved
- **THEN** they report that each token is set, without its value

#### Scenario: No secret store

- **WHEN** the researcher enables Slack on a machine with no usable secret store
- **THEN** enabling is refused and the reason names the missing secret store

### Requirement: Only linked users in allowed workspaces

VISTA SHALL act only on events from a workspace on its allowlist, and, when a channel allowlist
is set, only in those channels or in direct messages. Events from any other workspace SHALL be
ignored. Direct messages and taps on buttons in them SHALL be allowed whatever the channel
allowlist. A Slack user SHALL be able to trigger anything only once linked to a VISTA user from the
desktop app; every other user SHALL receive a polite refusal that tells them how to link, at most
once an hour per user. Linking SHALL pair a short-lived code shown to the Slack user with the
desktop app, so that a link cannot be created from Slack alone. Every event SHALL be verified by
the Slack SDK before any VISTA code handles it.

#### Scenario: An unlinked user in the same workspace

- **WHEN** a Slack user who is not linked DMs or mentions the bot
- **THEN** they are told politely that they are not linked and how to link, and no agent turn
  runs

#### Scenario: A workspace not on the allowlist

- **WHEN** an event arrives from a workspace not on the allowlist
- **THEN** it is ignored and logged, and nothing is posted

#### Scenario: A channel not on the allowlist

- **WHEN** a channel allowlist is set and a linked user mentions the bot in another channel
- **THEN** no agent turn runs and the user is told that the bot does not work in that channel

#### Scenario: Linking

- **WHEN** a Slack user DMs the bot `link` and the researcher enters the code shown into the
  desktop app's Slack settings within 10 minutes
- **THEN** that Slack user is linked to the researcher, and an expired or wrong code links nobody

### Requirement: Credentials never pass through Slack

A Slack-initiated turn SHALL act with the linked researcher's existing VISTA credentials. VISTA
SHALL NOT ask for a password, passcode, MFA code or token in Slack, and SHALL NOT include any
credential in a Slack message, file or log. A login form that a tool asks for during a
Slack-initiated turn SHALL be declined at once and SHALL NOT be shown in Slack; the reply SHALL
say which login is needed and that it is done in the desktop app.

#### Scenario: A tool that needs a login

- **WHEN** a linked user asks from Slack for something a tool needs a login for, such as the
  `agenthpc` server's SSH login, and there is none from the desktop app
- **THEN** no login form is shown in Slack and the reply says the login is done in the VISTA
  desktop app

### Requirement: Cluster actions need the requester's confirmation

During a Slack turn, submitting or cancelling an HPC job SHALL NOT happen until the conversation's
Slack user confirms it. The request SHALL be posted in the thread as a summary with Confirm and
Cancel buttons; for a job submission it SHALL show the system, account or project, nodes,
walltime and script path that the submission would actually use, worked out by VISTA rather than
described by the model. Only that user's Confirm SHALL count; anyone else who taps SHALL be told
so privately and the request SHALL remain open. A request unanswered after 10 minutes SHALL be
declined and shown as expired. A question a tool asks with a few fixed answers, such as a file
transfer's Approve or Decline, SHALL be posted as buttons under the same rules. Read-only tools,
job watches included, and work inside the code-execution sandbox SHALL run without
confirmation, subject to the lock.

#### Scenario: Submitting with confirmation

- **WHEN** a linked user asks from Slack to submit a job
- **THEN** a summary with system, account, nodes, walltime and script path is posted with Confirm
  and Cancel, and the job is submitted only after they tap Confirm

#### Scenario: The summary shows the job's defaults

- **WHEN** a linked user asks from Slack to submit a job without naming nodes or walltime
- **THEN** the summary shows the nodes and walltime the job's defaults give, not "default"

#### Scenario: Cancelling a job

- **WHEN** a linked user asks from Slack to cancel a job
- **THEN** the job and system are posted with Confirm and Cancel, and the job is cancelled only
  after they tap Confirm

#### Scenario: Another user taps Confirm

- **WHEN** someone other than the conversation's user taps Confirm
- **THEN** they are told privately that only that user can confirm, and nothing is submitted

#### Scenario: Confirmation expires

- **WHEN** a confirmation is not answered within 10 minutes
- **THEN** the action is not taken, the summary is marked expired, and later taps do nothing

#### Scenario: A file transfer

- **WHEN** a linked user asks from Slack to download a job's output and the transfer tool asks to
  approve it
- **THEN** its question is posted with Approve and Decline buttons, and the download goes ahead
  only after they tap Approve

#### Scenario: A read-only request

- **WHEN** a linked user asks for a job's status, or asks VISTA to watch a job
- **THEN** the answer is posted without any confirmation

### Requirement: Limits and lock for Slack-initiated jobs

The researcher SHALL be able to set, from the desktop app only, a maximum node count, a maximum
walltime and a maximum number of jobs per day for jobs submitted from Slack, whichever tool
submits them. A Slack request that would exceed any of them SHALL be refused before a
confirmation is offered, with the limit it exceeds; nodes and walltime SHALL be checked as the
submission would use them, the job's defaults included. Sending `lock` to the bot, in a DM or a
mention, SHALL disable every Slack-initiated side effect until the researcher unlocks from the
desktop app; Slack SHALL offer no way to unlock. Read-only requests SHALL still be answered while
locked. Limits and lock SHALL also be checked again when Confirm is tapped.

#### Scenario: Over the node limit

- **WHEN** the node limit is 4 and a linked user asks from Slack for an 8-node job
- **THEN** the request is refused, naming the 4-node limit, and no confirmation is posted

#### Scenario: Defaults over the limit

- **WHEN** the node limit is 4, a job's defaults ask for 8 nodes, and a linked user asks from
  Slack to submit it without naming a node count
- **THEN** the request is refused, naming the 4-node limit, and no confirmation is posted

#### Scenario: Over the daily job count

- **WHEN** the daily limit of Slack-submitted jobs has been reached
- **THEN** another Slack submission is refused, saying when the count resets

#### Scenario: Locking from Slack

- **WHEN** a linked user sends `lock`, then asks to submit a job
- **THEN** the submission is refused, saying Slack is locked and is unlocked from the desktop app

#### Scenario: Locked while a confirmation is open

- **WHEN** Slack is locked after a confirmation was posted and the requester then taps Confirm
- **THEN** the action is not taken

#### Scenario: Unlocking

- **WHEN** the researcher turns the lock off in the desktop app's Slack settings
- **THEN** Slack submissions are possible again, within the limits

### Requirement: One Slack thread is one conversation

Each Slack thread SHALL be one VISTA conversation, in the project the researcher chose for Slack
or, when none is chosen and they have more than one, the project they pick when VISTA asks, and
SHALL appear in the desktop app's conversation list marked as from Slack. A top-level DM or
mention SHALL start a new conversation, and replies SHALL go in its thread. Within a thread,
references such as "my last job", "the Lux job" or "rerun exp42 with 8 nodes" SHALL be resolved
against the researcher's recorded jobs. When a job or script is ambiguous, VISTA SHALL offer up to
five buttons rather than ask the user to type a path; only the requester's tap SHALL count.

#### Scenario: Choosing a project

- **WHEN** a linked user with several projects and no Slack project chosen starts a thread
- **THEN** VISTA asks in the thread which project it should go in, and answers in the one they tap

#### Scenario: One project

- **WHEN** a linked user with one project and no Slack project chosen starts a thread
- **THEN** VISTA answers in that project without asking

#### Scenario: Following up in a thread

- **WHEN** a linked user replies in the thread of an earlier answer
- **THEN** the agent answers with that conversation's history

#### Scenario: My last job

- **WHEN** a linked user asks "what's the status of my last job?"
- **THEN** the agent reports on the most recently submitted job recorded for that researcher

#### Scenario: An ambiguous job

- **WHEN** a request matches several recorded jobs or job folders
- **THEN** up to five buttons are offered and the choice tapped is used

#### Scenario: A message while a turn is running

- **WHEN** a linked user posts in a thread whose turn is still running
- **THEN** the message waits in the conversation's inbox, is marked with a reaction showing it
  was received, and is taken by that turn or the next one, not dropped

#### Scenario: Several messages while a turn is running

- **WHEN** a linked user posts three messages in a thread while its turn runs
- **THEN** all three are answered, in the order they were sent

#### Scenario: A restart with a message waiting

- **WHEN** VISTA restarts while a Slack message is waiting in a conversation's inbox
- **THEN** the message is still answered after VISTA starts again

### Requirement: A conversation stays in its channel

A conversation SHALL belong to the channel that started it. A conversation started from Slack
SHALL appear in the desktop app marked as from Slack and SHALL be read-only there: the desktop
app SHALL NOT send a message to it or answer its prompts, and the backend SHALL refuse either
from the desktop. Stopping its running turn from the desktop app SHALL still work. A conversation
started in the desktop app SHALL NOT post to Slack.

#### Scenario: Opening a Slack conversation on the desktop

- **WHEN** the researcher opens a conversation that was started from Slack
- **THEN** its messages are shown, the message box is disabled with a note that it continues in
  Slack, and its pending prompts cannot be answered there

#### Scenario: Stopping from the desktop

- **WHEN** a Slack conversation's turn is running and the researcher stops it from the desktop
  app
- **THEN** the turn stops and the thread's progress message says it was stopped

#### Scenario: A desktop conversation

- **WHEN** a job submitted from a desktop conversation ends
- **THEN** nothing is posted to Slack

### Requirement: Turns VISTA starts in a Slack conversation

A turn that VISTA starts by itself in a Slack conversation, such as when a background command or
a job watch reports, SHALL be posted in that conversation's thread like any other turn, and the
Slack safeguards SHALL apply to it. A confirmation it needs SHALL @mention the conversation's
Slack user, who alone can confirm it.

#### Scenario: A watched job ends

- **WHEN** a job watched in a Slack conversation ends and VISTA starts a turn with the result
- **THEN** the turn's progress message and answer are posted in that thread

#### Scenario: A resubmission nobody asked for in the moment

- **WHEN** a turn VISTA started in a Slack conversation tries to submit a job
- **THEN** the confirmation is posted in the thread @mentioning the conversation's user, and the
  job is submitted only if they tap Confirm within 10 minutes

### Requirement: Responsive replies

Every Slack event SHALL be acknowledged within Slack's 3-second deadline and processed
afterwards. Each turn SHALL post one progress message in the thread and update it in place as the
agent works, rather than post a message per step. When Slack rate-limits a call, VISTA SHALL wait
as Slack asks and retry.

#### Scenario: A slow turn

- **WHEN** a turn takes a minute of tool calls
- **THEN** one message in the thread shows its progress and becomes the answer

#### Scenario: A figure

- **WHEN** a turn in a Slack conversation displays an image
- **THEN** the image is attached in the thread after the answer

#### Scenario: Many figures

- **WHEN** a turn displays more than 10 files
- **THEN** the first 10 are attached and the thread says how many more are in the desktop app

#### Scenario: Rate limited

- **WHEN** Slack answers a call with HTTP 429
- **THEN** VISTA retries after the delay Slack gives, and the turn's answer is still posted

### Requirement: Replies suited to a phone

Replies SHALL be in Slack's markup and SHALL lead with a summary of one or two lines. Further
detail SHALL be behind a "Show more" button that expands the answer in place, once. Output that does not fit
in a Slack message SHALL be truncated with a pointer to ask for it; full output SHALL be attached
as a file only on request. No reply, however long, SHALL fail to post because of Slack's length
limits.

#### Scenario: A long answer

- **WHEN** the agent's answer is many times longer than a Slack message allows
- **THEN** a summary and a "Show more" button are posted, and tapping it turns the answer into
  as much of the full text as one message holds, posting only the remainder below it

#### Scenario: Show more tapped twice

- **WHEN** the researcher taps "Show more" and then looks for it again
- **THEN** the button is gone from the expanded answer, and nothing was posted twice

#### Scenario: Asking for the full log

- **WHEN** a linked user asks for a job's full output
- **THEN** it is attached to the thread as a file

### Requirement: Job notifications

A job submitted from a Slack conversation SHALL be watched, and when it reaches a terminal state
VISTA SHALL report it in that conversation's thread, @mentioning its Slack user. Each job SHALL
be reported once. A job that ended while VISTA was not running SHALL be reported once VISTA is
running again.

#### Scenario: A job finishes

- **WHEN** a job submitted from Slack completes or fails
- **THEN** its thread gets a reply @mentioning the user, with the job, system, final state and
  elapsed time

#### Scenario: Finished while offline

- **WHEN** a Slack-submitted job ended while VISTA was not running
- **THEN** it is reported after VISTA starts again, and only once

### Requirement: Credential expiry

For each linked researcher, VISTA SHALL send a direct message 24 hours and again 2 hours before
an HPC credential whose expiry it knows will expire, naming the affected systems. When a
Slack-initiated request needs a credential that has expired, the reply SHALL say which actions are
unavailable on which systems and that re-authentication is done in the desktop app. An expiry
that VISTA estimates rather than reads from the credential SHALL be labelled as estimated.

#### Scenario: A day before

- **WHEN** a researcher's Frontier credential expires in 24 hours
- **THEN** they receive one direct message naming Frontier and the time it expires

#### Scenario: After expiry

- **WHEN** a linked user asks from Slack to submit to a system whose credential has expired
- **THEN** the reply says submission, cancellation and outputs on that system are unavailable
  until they re-authenticate in the desktop app

### Requirement: App Home

The bot's Home tab SHALL show whether VISTA is online, the host machine, when it came online or
was last seen, the expiry of each HPC credential, whether Slack is locked, and the researcher's
recent Slack-submitted jobs with their states.

#### Scenario: Opening Home

- **WHEN** a linked user opens the bot's Home tab
- **THEN** it shows the status, host, credential expiry per system, lock state and recent jobs

### Requirement: Audit log

Each Slack-initiated action SHALL be logged with the Slack user, the linked VISTA user, the
request, the confirmation and who gave it, the HPC command it ran and the resulting job ID. The
log SHALL contain no credential.

#### Scenario: A confirmed submission

- **WHEN** a Slack-initiated submission is confirmed and submitted
- **THEN** the audit log has one record of the request, the confirmation and the job ID, and no
  token or password
