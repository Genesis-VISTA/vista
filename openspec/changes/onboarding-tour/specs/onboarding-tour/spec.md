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
  what VISTA is, and offers to start

#### Scenario: Opening a project

- **WHEN** the open-a-project step is shown
- **THEN** the projects page is shown and the coachmark points at the **Open** button of the
  AI Safety in Autonomous Labs project
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

#### Scenario: Chat before the first question

- **WHEN** the first-question step starts
- **THEN** a coachmark describes the chat screen before pointing at the composer

### Requirement: Connecting a model

The connect-a-model step SHALL first open Settings at the model provider section and point
at the provider choice and its credential field. It SHALL move on when the provider in effect
has a credential. It SHALL then point at the chat's model picker, and complete when the
researcher has chosen a model. Picking any model SHALL complete the step. The coachmark SHALL
suggest the chosen provider's default model by name, as reported by the backend
(`claude-sonnet` on AmSC i2 and AmSC MAG, `gpt-oss-120b` on OLCF Inference). A provider with
no default (Custom) SHALL be told to pick any model that its endpoint serves.

#### Scenario: Fresh install on AmSC i2

- **WHEN** a researcher on AmSC i2 with no credential reaches the connect-a-model step
- **THEN** Settings opens at the model provider section, and the coachmark asks for a
  credential
- **AND** once the credential is saved, the coachmark points at the model picker and suggests
  `claude-sonnet`
- **AND** the step completes when the researcher picks a model from the picker

#### Scenario: Picking something other than the suggestion

- **WHEN** a researcher on OLCF Inference picks a model other than `gpt-oss-120b`
- **THEN** the step completes

#### Scenario: Custom provider

- **WHEN** a researcher chooses Custom
- **THEN** the coachmark names no suggested model and asks them to pick or type one their
  endpoint serves

#### Scenario: Already configured

- **WHEN** a researcher whose provider has a credential and who has already chosen a model
  reaches the step
- **THEN** the step completes at once

### Requirement: Knowledge bases step

The knowledge bases step SHALL point at the navigation entry for knowledge bases, and wait
for the researcher to open the AI Safety Papers knowledge base. It SHALL then point at the
control for adding files to a knowledge base and explain it, moving on with **Next**. The
step SHALL NOT require adding or indexing any file.

#### Scenario: Opening AI Safety Papers

- **WHEN** a researcher on the knowledge bases step opens AI Safety Papers
- **THEN** a coachmark points at its **+ Add files** control and explains how to add content
- **AND** the step completes on **Next**, with no file added

### Requirement: Skills step

The skills step SHALL point at the navigation entry for the project's skills, and wait for
the researcher to open any skill to read its description. It SHALL then point at the control
that loads a skill into the project, and at **Import…**, each explained and moved past with
**Next**. The step SHALL NOT require loading or importing a skill.

#### Scenario: Reading a skill

- **WHEN** a researcher on the skills step opens a skill's details
- **THEN** a coachmark explains what a skill is, then points at **Load** and then at
  **Import…**
- **AND** the project's loaded skills are unchanged when the step completes

### Requirement: Files step with example files

The files step SHALL point at the navigation entry for files, and SHALL show, in the Files
page and only while that step is shown, three example files marked "Example": a markdown
report and a chart image among the outputs, and a CSV among the uploads. Opening an example
file SHALL preview it with the same previewer real files use. The step SHALL wait for the
researcher to open the example image and the example markdown report, then SHALL say to
continue with **Next** while the examples and the open preview stay shown. Example files
SHALL NOT be written to the project and SHALL NOT be visible to the agent. Their rows SHALL NOT
offer download, delete or show-in-chat; the preview's Download SHALL save the built-in example
under its own name and leave the tour where it was. They SHALL be gone once the step ends.

#### Scenario: Previewing examples

- **WHEN** a researcher on the files step opens the example chart and then the example report
- **THEN** each is previewed in the Files page, the chart as an image and the report as
  rendered markdown
- **AND** a coachmark says to choose **Next** when ready, with the last preview still shown
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

The HPC step SHALL point at the rail's resource status cards and wait for the researcher to
open any card. It SHALL then point at that card's way into Settings and say that clusters are
connected there, moving on with **Next**. It SHALL NOT require connecting a cluster.

#### Scenario: Opening a status card

- **WHEN** a researcher on the HPC step opens the Odo card
- **THEN** a coachmark points at the card's settings entry and explains that Odo is
  connected in Settings
- **AND** the step completes on **Next** without any cluster being connected

### Requirement: First question

The last step SHALL put this starter question in the chat composer, without sending it:
"What security risks do autonomous labs and LLM agents share, and what mitigations do the
papers propose? Cite the papers." The coachmark SHALL point at Send, and the researcher MAY
edit the question first. The step SHALL complete when a turn's answer finishes successfully.
If the turn is refused or fails, the coachmark SHALL say so and point at what fixes it (Settings
for a missing or rejected credential, the model picker for a missing model), and the step SHALL
complete on the next turn that succeeds. When the step completes, the tour SHALL say it is
finished, link to VISTA's documentation at <https://genesis-vista.github.io/>, and close
recorded as finished.

#### Scenario: A successful first answer

- **WHEN** a researcher sends the prefilled question and the answer finishes
- **THEN** the tour says it is finished, offers the documentation link, and closes as
  finished

#### Scenario: A refused first turn

- **WHEN** a researcher who skipped the connect-a-model step sends the question and the
  turn is refused for want of a credential
- **THEN** the coachmark says VISTA needs a model connected and points at Settings
- **AND** after a credential is added, the next successful answer completes the step

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
