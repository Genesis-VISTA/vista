## Purpose

Keeps each researcher's work their own on a shared VISTA: every stored row, live stream, file,
background task and running agent belongs to one user, and nobody else can see or touch it.

## ADDED Requirements

### Requirement: Everything a researcher makes has one owner

Projects, chat sessions, chat runs, campaign runs, HPC jobs, debate runs, background tasks,
Slack links, skills and knowledge bases the researcher makes or imports SHALL each belong to
exactly one user. Names SHALL be unique per owner, not across the server, so two researchers
may each have a project or a knowledge base called `hello-olcf`, and nothing one researcher
names reveals another's. The OLCF docs corpus and the skills VISTA seeds or bundles SHALL be
global: readable by everyone and changed by no one. Editing a bundled skill SHALL give the
editor their own copy, which takes its place for them alone; publishing a global skill SHALL
publish the publisher's copy. The OLCF docs corpus SHALL stay read-only.

#### Scenario: Same name, two owners

- **GIVEN** Alice has a project `hello-olcf` and a knowledge base `molten-salts`
- **WHEN** Bob creates a project `hello-olcf` and a knowledge base `molten-salts`
- **THEN** all four exist, each researcher sees only their own, and Bob was never told that the names were taken

#### Scenario: Editing a bundled skill makes a copy

- **WHEN** Bob edits a skill VISTA bundles
- **THEN** Bob's edit is saved as his own copy, which he sees in place of the bundled one, and Alice still sees the bundled skill unchanged

#### Scenario: The docs corpus is read-only

- **WHEN** Bob adds a PDF to the OLCF docs knowledge base
- **THEN** it is refused with the reason, and he can make a knowledge base of his own instead

#### Scenario: Existing desktop stores stay where they are

- **GIVEN** a desktop install with knowledge bases on disk from before this change
- **WHEN** the new version starts
- **THEN** every store is found where it was, with nothing moved

### Requirement: Another user's things do not exist

Every route that reads, changes, streams or deletes something owned SHALL answer `404` for
something another user owns, exactly as for something that does not exist, so ownership
cannot be probed. This SHALL hold for listings, chat-run event streams, pending questions to
the researcher, project files and downloads, and websocket or server-sent event routes.

#### Scenario: Bob opens Alice's chat stream

- **GIVEN** Alice has a chat run in progress
- **WHEN** Bob requests its event stream by its id
- **THEN** he receives `404`

#### Scenario: Bob lists projects

- **WHEN** Bob calls `GET /projects`
- **THEN** none of Alice's projects are in the list

### Requirement: Work without a request runs as its owner

Job watches, the campaign monitor, Slack turns, queued conversation inboxes and startup
recovery SHALL act as the owner of the row they work on: that owner's credentials, settings,
sandbox and model, never another user's and never the server's.

#### Scenario: A job watch after a restart

- **GIVEN** Alice's Odo job is being watched and the server restarts
- **WHEN** startup recovery resumes the watch
- **THEN** it reads the job's state with Alice's S3M token

### Requirement: Agents are per user

A running agent, its sandbox, its MCP credentials and its pending Globus logins SHALL belong
to one user. Credentials passed to MCP tools SHALL be only that user's. A settings change by one
user SHALL rebuild only that user's agents.

#### Scenario: Bob changes his model

- **GIVEN** Alice and Bob each have a conversation open
- **WHEN** Bob changes his default model
- **THEN** Alice's agent is not rebuilt, and her conversation continues on her model

#### Scenario: Two Globus logins at once

- **GIVEN** Alice and Bob both start connecting Globus for Odo
- **WHEN** each completes the login with their own code
- **THEN** each gets their own pair, and neither login overwrites the other

### Requirement: Desktop installs keep their data

On a desktop install everything that existed before this change SHALL belong to the implicit
user `local`, filled in at startup, without a reset prompt or any action by the researcher.

#### Scenario: Upgrading a desktop install

- **GIVEN** a desktop database from before this change, with projects and chats
- **WHEN** the new version starts on it
- **THEN** the researcher sees the same projects and chats, and the database was not refused
