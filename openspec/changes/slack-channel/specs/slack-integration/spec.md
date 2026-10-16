# Spec Delta

## Purpose

Lets a researcher talk to the VISTA agent running on their own machine from Slack, on desktop or
phone, with the same agent as the in-app chat and with safeguards suited to a device that can be
lost.

## ADDED Requirements

### Requirement: Slack is off until enabled

Slack integration SHALL be off by default. While it is off, VISTA SHALL open no connection to
Slack and SHALL behave exactly as without it. Enabling it SHALL require a bot token and an
app-level token, both held in the operating system's secret store and never in a plaintext file
or the database. Where no secret store is available, enabling SHALL be refused with the reason.
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
ignored. A Slack user SHALL be able to trigger anything only once linked to a VISTA user from the
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

#### Scenario: Lux without a recent desktop login

- **WHEN** a linked user asks from Slack to submit a Lux job and no Lux login is cached
- **THEN** no login form is shown in Slack and the reply says Lux needs a login in the VISTA
  desktop app

### Requirement: Side effects need the requester's confirmation

During a Slack-initiated turn, a tool that is not declared read-only SHALL NOT run until the
requesting Slack user confirms it. The request SHALL be posted in the thread as a summary with
Confirm and Cancel buttons; for a job submission it SHALL show the system, account or project,
nodes, walltime and script path. Only the requesting user's Confirm SHALL count; anyone else who
taps SHALL be told so privately and the request SHALL remain open. A request unanswered after 10
minutes SHALL be declined and shown as expired. Read-only tools SHALL run without confirmation.

#### Scenario: Submitting with confirmation

- **WHEN** a linked user asks from Slack to submit a job
- **THEN** a summary with system, account, nodes, walltime and script path is posted with Confirm
  and Cancel, and the job is submitted only after they tap Confirm

#### Scenario: Another user taps Confirm

- **WHEN** someone other than the requester taps Confirm
- **THEN** they are told privately that only the requester can confirm, and nothing is submitted

#### Scenario: Confirmation expires

- **WHEN** a confirmation is not answered within 10 minutes
- **THEN** the action is not taken, the summary is marked expired, and later taps do nothing

#### Scenario: A read-only request

- **WHEN** a linked user asks for a job's status
- **THEN** the answer is posted without any confirmation

### Requirement: Limits and lock for Slack-initiated jobs

The researcher SHALL be able to set, from the desktop app only, a maximum node count, a maximum
walltime and a maximum number of jobs per day for jobs submitted from Slack. A Slack request that
would exceed any of them SHALL be refused before a confirmation is offered, with the limit it
exceeds. Sending `lock` to the bot, in a DM or a mention, SHALL disable every Slack-initiated
side effect until the researcher unlocks from the desktop app; Slack SHALL offer no way to
unlock. Read-only requests SHALL still be answered while locked. Limits and lock SHALL also be
checked again when Confirm is tapped.

#### Scenario: Over the node limit

- **WHEN** the node limit is 4 and a linked user asks from Slack for an 8-node job
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

Each Slack thread SHALL be one VISTA conversation in the linked researcher's chosen project, and
SHALL appear in the desktop app's conversation list marked as from Slack. A top-level DM or
mention SHALL start a new conversation, and replies SHALL go in its thread. Within a thread,
references such as "my last job", "the Lux job" or "rerun exp42 with 8 nodes" SHALL be resolved
against the researcher's recorded jobs. When a job or script is ambiguous, VISTA SHALL offer up to
five buttons rather than ask the user to type a path; only the requester's tap SHALL count.

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

- **WHEN** a linked user posts in a thread whose previous turn is still running
- **THEN** the message is answered after that turn ends, not dropped

### Requirement: Responsive replies

Every Slack event SHALL be acknowledged within Slack's 3-second deadline and processed
afterwards. Each turn SHALL post one progress message in the thread and update it in place as the
agent works, rather than post a message per step. When Slack rate-limits a call, VISTA SHALL wait
as Slack asks and retry.

#### Scenario: A slow turn

- **WHEN** a turn takes a minute of tool calls
- **THEN** one message in the thread shows its progress and becomes the answer

#### Scenario: Rate limited

- **WHEN** Slack answers a call with HTTP 429
- **THEN** VISTA retries after the delay Slack gives, and the turn's answer is still posted

### Requirement: Replies suited to a phone

Replies SHALL be in Slack's markup and SHALL lead with a summary of one or two lines. Further
detail SHALL be behind a "Show more" button that posts it in the thread. Output that does not fit
in a Slack message SHALL be truncated with a pointer to ask for it; full output SHALL be attached
as a file only on request. No reply, however long, SHALL fail to post because of Slack's length
limits.

#### Scenario: A long answer

- **WHEN** the agent's answer is many times longer than a Slack message allows
- **THEN** a summary and a "Show more" button are posted, and tapping it posts the rest in the
  thread in as many messages as needed

#### Scenario: Asking for the full log

- **WHEN** a linked user asks for a job's full output
- **THEN** it is attached to the thread as a file

### Requirement: Job notifications

When a job submitted from Slack reaches a terminal state, VISTA SHALL notify the requester with an
@mention, either in the thread it was submitted from or by direct message, as the researcher
chooses; the default SHALL be direct message. A setting SHALL extend this to jobs the researcher
submits from the desktop app, notified by direct message. Each job SHALL be notified once. A job
that ended while VISTA was not running SHALL be notified once VISTA is running again.

#### Scenario: A job finishes

- **WHEN** a job submitted from Slack completes or fails
- **THEN** the requester is @mentioned with the job, system, final state and elapsed time

#### Scenario: Finished while offline

- **WHEN** a Slack-submitted job ended while VISTA was not running
- **THEN** the notification is sent after VISTA starts again, and only once

#### Scenario: Desktop jobs

- **WHEN** the desktop-job setting is on and a job submitted from the desktop app ends
- **THEN** the researcher is notified by direct message

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

### Requirement: Offline and online notices

When VISTA stops gracefully, the application quits, or the system is about to sleep, each linked
researcher SHALL receive the direct message "Your VISTA agent is going offline (host: <machine>).
Messages sent while it's offline won't be received." When VISTA reconnects after such a notice, it
SHALL send a short back-online message, and only then. Reconnects within 2 minutes of the previous
notice SHALL send nothing.

#### Scenario: Quitting

- **WHEN** the researcher quits VISTA with Slack enabled
- **THEN** their DM receives the offline notice naming the machine

#### Scenario: Relaunching

- **WHEN** VISTA reconnects after having sent an offline notice
- **THEN** their DM receives "VISTA is back online"

#### Scenario: A brief network drop

- **WHEN** the Slack connection drops and reconnects without an offline notice
- **THEN** no message is sent

### Requirement: App Home

The bot's Home tab SHALL show whether VISTA is online, the host machine, when it came online or
was last seen, the expiry of each HPC credential, whether Slack is locked, and the researcher's
recent Slack-submitted jobs with their states.

#### Scenario: Opening Home

- **WHEN** a linked user opens the bot's Home tab
- **THEN** it shows the status, host, credential expiry per system, lock state and recent jobs

### Requirement: One machine handles Slack

A Slack app SHALL be handled by at most one VISTA instance. An instance SHALL connect only when
its "handle Slack on this machine" setting is on and no other instance is active for that app.
An instance that finds another active SHALL stay disconnected and SHALL say in its settings which
host is active and when it was last seen, with a way to take over. An instance that is taken over
SHALL disconnect.

#### Scenario: A second machine

- **WHEN** the same tokens are configured on a second machine while the first is active
- **THEN** the second stays disconnected and its settings name the first machine

#### Scenario: Taking over

- **WHEN** the researcher takes over on the second machine
- **THEN** the first disconnects, and Slack events are handled by the second

### Requirement: Background mode and sleep

The researcher SHALL be able to turn on a background mode from the desktop app in which VISTA's
services start at login and keep running when the window is closed. An optional setting, off by
default, SHALL keep the system from idle sleep while Slack is enabled.

#### Scenario: Window closed

- **WHEN** background mode is on and the researcher closes the VISTA window
- **THEN** a DM to the bot is still answered

#### Scenario: After login

- **WHEN** background mode is on and the researcher logs in after a restart
- **THEN** VISTA's services start without opening a window, and Slack connects

### Requirement: Audit log

Each Slack-initiated action SHALL be logged with the Slack user, the linked VISTA user, the
request, the confirmation and who gave it, the HPC command it ran and the resulting job ID. The
log SHALL contain no credential.

#### Scenario: A confirmed submission

- **WHEN** a Slack-initiated submission is confirmed and submitted
- **THEN** the audit log has one record of the request, the confirmation and the job ID, and no
  token or password
