# settings-modal Specification

## Purpose
Organises VISTA's settings into a few sections a researcher can navigate between, saves
each change as it is made, and works in a narrow window as well as a wide one.

## Requirements

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

### Requirement: Opened at a named section

Settings SHALL be able to be opened by parts of the application other than the rail's
settings button, at a section that part names. It SHALL then show that section's settings,
including in a narrow window, where the section is shown in place of the navigation list.
Opening Settings this way SHALL NOT change how it opens from the rail's settings button.

#### Scenario: Opened by the tour at the model provider

- **WHEN** the guided tour opens Settings at the model provider section
- **THEN** the model provider section is shown and selected in the navigation

#### Scenario: Narrow window

- **WHEN** Settings is opened at the model provider section in a half-width window
- **THEN** the model provider section is shown rather than the navigation list

### Requirement: Tour replay and documentation

The Research profile section SHALL offer **Replay tutorial**, which closes Settings and starts
the guided tour from its welcome step, and a **Documentation** link to
<https://genesis-vista.github.io/>, which opens in the system browser.

#### Scenario: Replaying the tour

- **WHEN** a researcher chooses **Replay tutorial** in Settings
- **THEN** Settings closes and the tour opens at the welcome step

#### Scenario: Opening the documentation

- **WHEN** a researcher chooses **Documentation** in Settings
- **THEN** VISTA's documentation opens in the system browser, and VISTA stays as it was

### Requirement: About section

The settings navigation SHALL end with an About section. About SHALL show the installed
version and commit, each copyable, a link to that version's release notes, and the update
state: when checks are off or not run for this build, why; otherwise the time of the last
check, a Check now action, and the newer release when there is one with its notes link. About
SHALL hold the two update settings: check for updates, and include prereleases. Like every
other setting, they SHALL save as they are changed.

#### Scenario: Reading the version

- **WHEN** a researcher opens About on 0.2.3
- **THEN** it shows version 0.2.3 and its commit, each with a copy button

#### Scenario: An update is available

- **WHEN** 0.2.5 has been found and the researcher opens About
- **THEN** it shows that 0.2.5 is available with its release notes link and an Update action

#### Scenario: Turning checks off

- **WHEN** the researcher turns off check for updates
- **THEN** the setting saves at once and About says update checks are off

### Requirement: Settings marks an available update

While a newer release is available, the rail's settings button SHALL carry a small marker,
and About's navigation entry SHALL carry the same marker. Nothing else in the interface SHALL
announce the update, and the marker SHALL NOT be dismissible other than by updating or turning
checks off.

#### Scenario: Marker shown

- **WHEN** the backend reports 0.2.5 as available
- **THEN** the settings button and the About entry each show the marker, and no banner or
  toast appears

#### Scenario: Marker cleared

- **WHEN** the researcher turns update checks off
- **THEN** the marker disappears

### Requirement: Remote directory filled in from the cluster's project

When the modal knows the project of Odo, Frontier or Lux, from the cluster status's credential
check (the cluster's S3M token's `project` claim), it SHALL
fill that cluster's remote directory with the project's folder:

| Cluster | Folder |
|---|---|
| Odo | `/gpfs/wolf2/olcf/<folder>/proj-shared/vista` |
| Frontier, Lux | `/lustre/orion/<folder>/proj-shared/vista` |

`<folder>` SHALL be the project lowercased and cut at its first dash (`gen150-vista` gives
`gen150`).

It SHALL fill the field only when the field is empty, or when it holds that cluster's folder
for a different project. It SHALL never change any other value. It SHALL NOT fill a field
whose edit has not been saved yet. It SHALL do this whenever the modal has the project, both
after a token is saved and when the modal opens with one already saved. The fill
SHALL be saved, marked and announced like a typed change.

While the field holds the folder it filled for the current project, a note under it SHALL say
where the value came from:
- "Filled from your S3M token's project (gen150 folder inferred from gen150-vista)." when the
  folder differs from the project;
- "Filled from your S3M token's project, chm243." when it does not.

Before a project is known, each of the three clusters' hints SHALL begin "Filled from your S3M
token once you add one." The placeholder SHALL stay as it is.
Removing a token SHALL leave the directory as it is.

#### Scenario: A token from a subproject

- **GIVEN** Odo's remote directory is empty
- **WHEN** a researcher saves an Odo token whose project is `gen150-vista`
- **THEN** the directory becomes `/gpfs/wolf2/olcf/gen150/proj-shared/vista`, is saved, and the
  note reads "Filled from your S3M token's project (gen150 folder inferred from gen150-vista)."

#### Scenario: A token from a project with no dash

- **GIVEN** Frontier's remote directory is empty
- **WHEN** a researcher saves a Frontier token whose project is `chm243`
- **THEN** the directory becomes `/lustre/orion/chm243/proj-shared/vista` and the note reads
  "Filled from your S3M token's project, chm243."

#### Scenario: A token from another project replaces a filled folder

- **GIVEN** Odo's remote directory is `/gpfs/wolf2/olcf/gen150/proj-shared/vista`
- **WHEN** a researcher saves an Odo token whose project is `stf218`
- **THEN** the directory becomes `/gpfs/wolf2/olcf/stf218/proj-shared/vista`

#### Scenario: A typed directory is kept

- **GIVEN** Odo's remote directory is `/gpfs/wolf2/olcf/gen150/proj-shared/sam/v2`
- **WHEN** a researcher saves an Odo token from any project
- **THEN** the directory is unchanged and no note is shown

#### Scenario: Editing a filled directory

- **GIVEN** Odo's remote directory was filled for `gen150-vista`
- **WHEN** the researcher changes it to another path
- **THEN** the note is no longer shown, and the new path is never replaced while the token's
  project stays the same

#### Scenario: Lux from its token

- **GIVEN** Lux's remote directory is empty
- **WHEN** a researcher saves a Lux S3M token whose project is `stf218`
- **THEN** the directory becomes `/lustre/orion/stf218/proj-shared/vista` and the note reads
  "Filled from your S3M token's project, stf218."

#### Scenario: A token saved before this change

- **GIVEN** an Odo token was saved and the remote directory left empty before this change
- **WHEN** the researcher opens Odo's settings
- **THEN** the directory is filled from the token's project

#### Scenario: Removing the token

- **GIVEN** Odo's remote directory was filled from its token
- **WHEN** the researcher removes the token
- **THEN** the directory is unchanged
