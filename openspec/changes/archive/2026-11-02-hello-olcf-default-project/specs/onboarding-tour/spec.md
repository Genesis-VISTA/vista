## MODIFIED Requirements

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
  at the `hello-olcf` project card, at **New project**, and at the card's **Open** button
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
- **AND** on **Next**, the coachmark points at OLCF User Documentation

#### Scenario: Arriving in chat

- **WHEN** opening a project brings the researcher to the chat
- **THEN** a coachmark describes the chat screen before pointing at the composer

## ADDED Requirements

### Requirement: Knowledge bases step opens the OLCF docs

The knowledge bases step SHALL point at the navigation entry for knowledge bases, then,
after the screen's overview, at the list of knowledge bases, and wait for the researcher to
open the OLCF User Documentation knowledge base. It SHALL then point at the knowledge base's
description, then at its documents, then at the control for adding files, each explained and
moved past with **Next**. The step SHALL NOT require adding or indexing any file.

#### Scenario: Opening OLCF User Documentation

- **WHEN** a researcher on the knowledge bases step opens OLCF User Documentation
- **THEN** coachmarks point at its description and at its documents, then at its **+ Add
  files** control, explaining how to add content
- **AND** the step completes on **Next**, with no file added

## MODIFIED Requirements

### Requirement: Skills step

The skills step SHALL point at the navigation entry for the project's skills, then, after
the screen's overview, at the skills loaded in the project and at those on this computer,
and wait for the researcher to open any skill. Wherever a coachmark names a skill, it SHALL
name the skill it points at. While the skill is open, the coachmark SHALL point at its
instructions and wait for the researcher to close it. It SHALL then point at the control that
loads a skill into the project, and at **Import…**, each explained and moved past with
**Next**. The step SHALL NOT require loading or importing a skill.

#### Scenario: Reading a skill

- **WHEN** a researcher on the skills step opens a skill's details
- **THEN** a coachmark points at the skill's instructions while it stays open
- **AND** once it is closed, the coachmarks point at **Load** and then at **Import…**
- **AND** the project's loaded skills are unchanged when the step completes

#### Scenario: The default project's skills

- **WHEN** the skills step is shown in `hello-olcf`
- **THEN** the coachmark over the loaded skills says the project loads its hello skills and
  `hpc-skill-author`, and any coachmark that names a skill names the one it points at

### Requirement: First question

The last step SHALL put this starter question in the chat composer, without sending it:
"How do I request GPUs for a batch job on Frontier, and which queue should a short test run
use? Cite the OLCF docs." The coachmark SHALL point at the composer, and the researcher MAY
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

## REMOVED Requirements

### Requirement: Knowledge bases step

**Reason**: The step now opens the OLCF docs knowledge base instead of AI Safety Papers.
A requirement's scenarios cannot be renamed in place, so it is replaced by "Knowledge bases
step opens the OLCF docs".
**Migration**: Same step, same controls. Only the knowledge base it waits for changes.
