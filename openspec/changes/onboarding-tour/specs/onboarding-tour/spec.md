## Purpose

Guides a researcher through VISTA the first time they open it: opening a project,
connecting a model, and finding knowledge bases, skills, files and HPC, ending on a first
question the agent answers.

## ADDED Requirements

### Requirement: The tour opens once per installation

When VISTA's main interface loads and the installation has no recorded tour state, the
guided tour SHALL open at its first step. This SHALL apply to an installation that existed
before the tour did. Once the tour is finished or exited, it SHALL NOT open by itself again.
The tour state SHALL be stored with the installation's local settings, so it survives
restarting VISTA and installing a newer version, and it is gone when the local data is reset.

#### Scenario: Fresh install

- **WHEN** a researcher opens VISTA for the first time after installing it
- **THEN** the tour opens at the welcome step

#### Scenario: Upgrading an installation that predates the tour

- **WHEN** a researcher who has used an earlier version opens a version that has the tour
- **THEN** the tour opens at the welcome step

#### Scenario: Tour already finished

- **WHEN** a researcher who finished or exited the tour opens VISTA again
- **THEN** the tour does not open

#### Scenario: After a data reset

- **WHEN** a researcher backs up and resets incompatible local data and VISTA starts fresh
- **THEN** the tour opens at the welcome step

### Requirement: Coachmarks with skip and exit

Each step SHALL show its explanation in a coachmark placed beside the control it is about,
with the rest of the window dimmed and that control left visible. A step with no single
control SHALL show its coachmark in the middle of the window. Every coachmark SHALL offer
**Skip this step** and **Exit tutorial**. Skipping SHALL move to the next step without the
step's action being done. Exiting SHALL close the tour and record it as exited. The tour
SHALL be usable from the keyboard, and Escape SHALL do what **Exit tutorial** does.

#### Scenario: Skipping a step

- **WHEN** a researcher chooses **Skip this step** on the knowledge bases step
- **THEN** the tour moves to the skills step

#### Scenario: Exiting

- **WHEN** a researcher chooses **Exit tutorial** on any step
- **THEN** the tour closes, and it does not open by itself on the next launch

### Requirement: Steps and their order

The tour SHALL present these steps in this order: welcome, open a project, connect a model,
knowledge bases, skills, files, HPC, first question. A step SHALL wait for the action it
teaches before moving on, except where a requirement below allows a plain **Next**. A step
whose only waited-for action is already done when the step is reached SHALL complete at
once.

#### Scenario: Welcome

- **WHEN** the tour opens
- **THEN** a coachmark in the middle of the window welcomes the researcher to VISTA, says
  what VISTA is and that the tour takes about eight minutes, and offers to start
- **AND** on start, a coachmark points at the navigation rail and says how it is arranged

#### Scenario: Opening a project

- **WHEN** the open-a-project step is shown
- **THEN** the projects page is shown, and after its overview the coachmarks point in turn
  at the AI Safety in Autonomous Labs project card, at **New project**, and at the card's
  **Open** button
- **AND** the step completes when any project becomes the active project, which leaves the
  researcher in that project's chat

#### Scenario: A project already open

- **WHEN** the open-a-project step is reached while a project is already active
- **THEN** the step completes at once

### Requirement: Each screen opens with an overview

When the tour first brings the researcher to a screen (projects, knowledge bases, skills,
files and chat), it SHALL first show a coachmark that describes the screen as a whole, with
nothing dimmed, so the whole screen can be seen. It SHALL move on to that screen's specific
controls only on **Next**. For a step that starts at the navigation entry, the overview SHALL
come once the researcher has reached the screen.

#### Scenario: Arriving at knowledge bases

- **WHEN** a researcher on the knowledge bases step clicks the knowledge bases navigation entry
- **THEN** a coachmark describes the knowledge bases screen, and nothing on it is dimmed
- **AND** on **Next**, the coachmark points at AI Safety Papers

#### Scenario: Arriving in chat

- **WHEN** opening a project brings the researcher to the chat
- **THEN** a coachmark describes the chat screen before pointing at the composer

### Requirement: Each screen is explained before it is used

After a screen's overview, the tour SHALL point at the screen's main regions one at a time,
each moved past with **Next**, before it asks the researcher to do anything there. When an
action opens something (a knowledge base, a skill, a file preview, a cluster's status card,
an answer), the next coachmark SHALL describe what was opened as a whole before pointing at
any one part of it.

#### Scenario: Opening a cluster's card

- **WHEN** a researcher on the HPC step opens the Odo card
- **THEN** the coachmark points at the whole card and lists what Odo needs to be ready
- **AND** on **Next**, it points at one check and says what a red check means
- **AND** on **Next**, it points at Settings and Recheck

### Requirement: Connecting a model

The connect-a-model step SHALL start in the chat: its overview, then the composer, the
artifacts and activity panel, and the model picker, each on **Next**. It SHALL then open
Settings at the model provider section and describe the section, then the provider choice,
then point at the credential field, and move on when the provider in effect has a
credential; these Settings coachmarks SHALL NOT be shown when the provider already has one.
It SHALL then point at the chat's model picker and wait for the researcher to choose a model,
then say the model now answers in every chat, on **Next**. Picking any model SHALL complete the step. The coachmark SHALL
suggest the chosen provider's default model by name, as reported by the backend
(`claude-sonnet` on AmSC i2 and AmSC MAG, `gpt-oss-120b` on OLCF Inference). A provider with
no default (Custom) SHALL be told to pick any model that its endpoint serves.

#### Scenario: Fresh install on AmSC i2

- **WHEN** a researcher on AmSC i2 with no credential reaches the connect-a-model step
- **THEN** after the chat's coachmarks, Settings opens at the model provider section, and
  the coachmarks describe it and then ask for a credential
- **AND** once the credential is saved, the coachmark points at the model picker and suggests
  `claude-sonnet`
- **AND** once the researcher picks a model, a coachmark says it is set, and the step
  completes on **Next**

#### Scenario: Picking something other than the suggestion

- **WHEN** a researcher on OLCF Inference picks a model other than `gpt-oss-120b`
- **THEN** the step moves on as it does for the suggestion

#### Scenario: Custom provider

- **WHEN** a researcher chooses Custom
- **THEN** the coachmark names no suggested model and asks them to pick or type one their
  endpoint serves

#### Scenario: Already configured

- **WHEN** a researcher whose provider has a credential and who has already chosen a model
  reaches the step
- **THEN** Settings is not opened and the picker is not waited for; only the chat's
  coachmarks and the closing "model set" coachmark are shown

### Requirement: Knowledge bases step

The knowledge bases step SHALL point at the navigation entry for knowledge bases, then,
after the screen's overview, at the list of knowledge bases, and wait for the researcher to
open the AI Safety Papers knowledge base. It SHALL then point at the knowledge base's
description, then at its papers, then at the control for adding files, each explained and
moved past with **Next**. The
step SHALL NOT require adding or indexing any file.

#### Scenario: Opening AI Safety Papers

- **WHEN** a researcher on the knowledge bases step opens AI Safety Papers
- **THEN** coachmarks point at its description and at its papers, then at its **+ Add
  files** control, explaining how to add content
- **AND** the step completes on **Next**, with no file added

### Requirement: Skills step

The skills step SHALL point at the navigation entry for the project's skills, then, after
the screen's overview, at the skills loaded in the project and at those on this computer,
and wait for the researcher to open any skill. While the skill is open, the coachmark SHALL
point at its instructions and wait for the researcher to close it. It SHALL then point at the
control that loads a skill into the project, and at **Import…**, each explained and moved
past with **Next**. The step SHALL NOT require loading or importing a skill.

#### Scenario: Reading a skill

- **WHEN** a researcher on the skills step opens a skill's details
- **THEN** a coachmark points at the skill's instructions while it stays open
- **AND** once it is closed, the coachmarks point at **Load** and then at **Import…**
- **AND** the project's loaded skills are unchanged when the step completes

### Requirement: Files step with example files

The files step SHALL point at the navigation entry for files, and SHALL show, in the Files
page and only while that step is shown, three example files marked "Example": a markdown
report and a chart image among the outputs, and a CSV among the uploads. Opening an example
file SHALL preview it with the same previewer real files use. After the screen's overview,
the step SHALL point at the outputs and at the uploads, then wait for the researcher to open
the example image, describe its preview, then wait for the example markdown report, and
describe its preview, moving on with **Next** while the examples and the open preview stay
shown. Example files
SHALL NOT be written to the project and SHALL NOT be visible to the agent. Their rows SHALL NOT
offer download, delete or show-in-chat; the preview's Download SHALL save the built-in example
under its own name and leave the tour where it was. They SHALL be gone once the step ends.

#### Scenario: Previewing examples

- **WHEN** a researcher on the files step opens the example chart and then the example report
- **THEN** each is previewed in the Files page, the chart as an image and the report as
  rendered markdown
- **AND** after each, a coachmark describes its preview, the last with the preview still
  shown
- **AND** the step completes on **Next**

#### Scenario: Downloading an example

- **WHEN** a researcher chooses Download in an example's preview
- **THEN** the example is saved under its own file name, and the tour stays on the same
  coachmark

#### Scenario: Examples never reach the project

- **WHEN** the files step ends, by completing, skipping or exiting
- **THEN** the Files page lists only the project's real files, and the project's file
  storage contains no example file

### Requirement: HPC step

The HPC step SHALL point at the rail's compute resources and describe them, on **Next**,
then wait for the researcher to open the Odo card. It SHALL then point at the whole card,
then at one of its checks, then at its way into Settings and its Recheck, saying that
clusters are connected in Settings, each on **Next**. It SHALL NOT require connecting a cluster.

#### Scenario: Opening a status card

- **WHEN** a researcher on the HPC step opens the Odo card
- **THEN** the coachmarks describe the card and one check, then point at the card's
  settings entry and explain that Odo is connected in Settings
- **AND** the step completes on **Next** without any cluster being connected

### Requirement: First question

The last step SHALL put this starter question in the chat composer, without sending it:
"What security risks do autonomous labs and LLM agents share, and what mitigations do the
papers propose? Cite the papers." The coachmark SHALL point at the composer, and the researcher MAY
edit the question first. Once it is sent, the coachmark SHALL point at the activity panel
while the turn runs, and when the answer finishes successfully, at the answer, on **Next**.
If the turn is refused or fails, the coachmark SHALL say so and point at what fixes it (Settings
for a missing or rejected credential, the model picker for a missing model), and the step SHALL
go on when the next turn starts. After the answer, a coachmark in the middle of the window
SHALL say the tour is finished, link to VISTA's documentation at
<https://genesis-vista.github.io/>, say where Replay tutorial is, and close recorded as
finished on **Done**.

#### Scenario: A successful first answer

- **WHEN** a researcher sends the prefilled question and the answer finishes
- **THEN** coachmarks point at the activity panel while it runs and at the answer once it
  finishes
- **AND** the tour then says it is finished, offers the documentation link, and closes as
  finished

#### Scenario: A refused first turn

- **WHEN** a researcher who skipped the connect-a-model step sends the question and the
  turn is refused for want of a credential
- **THEN** the coachmark says VISTA needs a model connected and points at Settings
- **AND** after a credential is added, sending again goes on to the activity panel

### Requirement: Resuming and replaying

Quitting VISTA during the tour SHALL leave it in progress. The next launch SHALL reopen it at
the step that was showing. Replaying from Settings SHALL start the tour again from the welcome
step, whatever its recorded state.

#### Scenario: Quitting mid-tour

- **WHEN** a researcher quits VISTA while the skills step is showing, and opens it again
- **THEN** the tour reopens at the skills step

#### Scenario: Replaying

- **WHEN** a researcher who finished the tour chooses **Replay tutorial** in Settings
- **THEN** Settings closes and the tour opens at the welcome step

### Requirement: The navigation rail is expanded during the tour

While the tour is open, the navigation rail SHALL be expanded so its labels are visible. When
the tour closes, the rail SHALL return to the researcher's own collapse choice.

#### Scenario: Collapsed rail

- **WHEN** the tour opens for a researcher who keeps the rail collapsed
- **THEN** the rail is expanded while the tour runs, and collapsed again when it closes

### Requirement: The tour can be turned off by configuration

When VISTA is started with `VISTA_BACKEND_ONBOARDING_TOUR=false`, the tour SHALL NOT open by
itself and its recorded state SHALL be left unchanged. Replaying from Settings SHALL still work.

#### Scenario: Automated run

- **WHEN** an end-to-end test starts VISTA with fresh data and
  `VISTA_BACKEND_ONBOARDING_TOUR=false`
- **THEN** no tour is shown over the application
