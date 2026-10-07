## Purpose

Organises VISTA's settings into a few sections a researcher can navigate between, saves
each change as it is made, and works in a narrow window as well as a wide one.

## ADDED Requirements

### Requirement: Sections and navigation

The settings modal SHALL present its settings as separate sections chosen from a
navigation list. The list SHALL contain, in order:
- Appearance;
- Agent;
- Resources, listed as a tree of institution, then facility, then resource: ORNL › OLCF ›
  Odo, Frontier and Lux; LBNL › NERSC › Perlmutter.

Every supported resource SHALL appear in the tree whether or not it is shown in the
sidebar. Each resource entry SHALL show the same status dot the rail shows for it, and an
entry for a resource hidden from the sidebar SHALL say so. Selecting an entry SHALL show
that section's settings and nothing else. The tree SHALL NOT list a facility or
institution that has no supported resource.

The modal SHALL NOT show the signed-in account.

#### Scenario: Opened from the rail's settings button

- **WHEN** a researcher opens settings from the rail's settings button
- **THEN** the navigation lists Appearance, Agent and the resource tree, and the Agent
  section is shown

#### Scenario: A hidden resource is still listed

- **WHEN** a researcher has hidden Perlmutter from the sidebar and opens settings
- **THEN** Perlmutter is listed under LBNL › NERSC and marked as hidden from the sidebar

#### Scenario: No account block

- **WHEN** the settings modal is open
- **THEN** it does not show which account is signed in

### Requirement: Changes save as they are made

Every setting the modal edits SHALL be saved without a Save action:
- a text field SHALL save once the researcher has stopped typing for a short pause;
- a secret field (an API key or a token) SHALL save when it loses focus or when a value is
  pasted into it, never part-way through typing;
- a switch or a choice SHALL save when it changes.

A field cleared to empty SHALL be saved as not set. The modal SHALL offer Close and no
Save or Cancel. Closing the modal while a save is pending SHALL still complete that save.
Connecting a Globus account and choosing an appearance SHALL take effect at once, as
before.

#### Scenario: Typing a remote directory

- **WHEN** a researcher types a Frontier remote directory and pauses
- **THEN** the directory is saved without pressing anything, and is still there after a
  reload

#### Scenario: Pasting a token

- **WHEN** a researcher pastes an S3M token into Odo's token field
- **THEN** the token is saved once, and Odo is rechecked

#### Scenario: Typing a token by hand

- **WHEN** a researcher types a token one character at a time
- **THEN** nothing is saved until the field loses focus

#### Scenario: Closing during a save

- **WHEN** a researcher closes the modal straight after changing a field
- **THEN** the change is saved

### Requirement: Each field shows its own save

Each field SHALL show the outcome of its own save at the field itself, and the modal
SHALL have no save indicator of its own:
- while a save is in flight, the field SHALL show nothing, unless the save has taken
  longer than about a second, when it SHALL show that it is still saving;
- a saved field SHALL show a check mark, which SHALL fade after about two seconds and
  SHALL go at once when the field is edited again;
- a failed field SHALL show a cross until a later save of that field succeeds.

A switch or a choice SHALL show its mark beside the control. A failed save SHALL also
show, beneath the field, why it failed, and SHALL keep the value the researcher entered.
A section holding a failed field SHALL be marked with a cross in the navigation, and
selecting it SHALL show that section with the failed field focused. Each outcome SHALL
also be announced to assistive technology, naming the field.

#### Scenario: A save succeeds

- **WHEN** a field is saved successfully
- **THEN** that field shows a check mark, which fades, and no other field changes

#### Scenario: A slow save

- **WHEN** a field's save is still in flight after about a second
- **THEN** that field shows that it is still saving, until the save completes

#### Scenario: A save fails

- **WHEN** saving the Perlmutter remote directory fails
- **THEN** the directory field shows a cross, says why, and still holds what was typed

#### Scenario: A save fails after leaving the section

- **WHEN** the Perlmutter remote directory's save fails after the researcher has moved to
  the Agent section
- **THEN** Perlmutter is marked with a cross in the navigation, and selecting it shows the
  Perlmutter section with the directory field focused

#### Scenario: Fixed after failing

- **WHEN** a field whose save failed is saved successfully
- **THEN** its cross, its message and the navigation's cross go away, and it shows a check
  mark

### Requirement: Narrow windows

When the modal is too narrow to show the navigation beside a section, it SHALL show the
navigation on its own; choosing an entry SHALL show that section across the full width,
with a control to return to the navigation.

#### Scenario: Half-width window

- **WHEN** the settings modal is opened in a window too narrow for both columns
- **THEN** only the navigation list is shown, choosing Frontier shows Frontier's settings
  full-width, and a back control returns to the list
