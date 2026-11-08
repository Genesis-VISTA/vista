## ADDED Requirements

### Requirement: Artifacts feed

The Artifacts tab SHALL list every file displayed in the open conversation, newest first,
built from VISTA's record of displayed files so that it is the same after a reload. Images
SHALL show inline and open a larger view on click; other files SHALL expand in place with the
Files page's preview for their type. Every item SHALL link to the file on the Files page and
to the turn that displayed it. Non-file tool results (HTML results, the prediction summary
and references) SHALL keep showing from the latest result, as before.

#### Scenario: Several figures in one chat

- **WHEN** a conversation's runs display `a.png`, then `b.csv`, then `c.png`
- **THEN** the Artifacts tab SHALL list `c.png`, `b.csv`, `a.png` in that order
- **AND** after a reload it SHALL list the same items in the same order

#### Scenario: Feed is per conversation

- **WHEN** another conversation in the same project displays a file
- **THEN** it SHALL NOT appear in this conversation's feed

#### Scenario: New item during a run

- **WHEN** the agent displays a file during a run
- **THEN** it SHALL appear at the top of the feed without a reload

#### Scenario: Changed file

- **WHEN** a feed item's file has changed on disk since it was displayed
- **THEN** the item SHALL show its name and "file has changed on disk", with no preview
- **AND** it SHALL still link to the file on the Files page and to its turn

#### Scenario: Missing file

- **WHEN** a feed item's file no longer exists
- **THEN** the item SHALL show its name and "file no longer exists", with no preview and no Files link

### Requirement: Open a conversation at a turn

A conversation SHALL be openable by URL naming the conversation and, optionally, a run.
Opening a run SHALL scroll to and highlight that turn's answer, and, when the URL names the
display the user came from, show the Artifacts tab with that file's card scrolled to and
highlighted.

#### Scenario: Jump from the Files page

- **WHEN** the user follows "Show in chat" for a file on the Files page
- **THEN** chat SHALL open that conversation, scroll to and highlight the turn that displayed the file
- **AND** the Artifacts tab SHALL be shown, with that file's card scrolled to and highlighted

#### Scenario: Display with no card

- **WHEN** the URL names a display the conversation's feed has no record of
- **THEN** only the turn SHALL be highlighted

#### Scenario: Unknown run

- **WHEN** the URL names a conversation that exists and a run it does not contain
- **THEN** the conversation SHALL open at its latest message with no highlight

#### Scenario: Unknown conversation

- **WHEN** the URL names a conversation that does not exist in the active project
- **THEN** chat SHALL open as it would without the URL, and say that the conversation was not found
