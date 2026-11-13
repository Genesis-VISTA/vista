## MODIFIED Requirements

### Requirement: Steps and their order

The tour SHALL present these steps in this order: welcome, open a project, connect a model,
knowledge bases, skills, files, HPC, collaboration, first question. A step SHALL wait for the
action it teaches before moving on, except where a requirement below allows a plain **Next**. A
step whose only waited-for action is already done when the step is reached SHALL complete at
once.

#### Scenario: Welcome

- **WHEN** the tour opens
- **THEN** a coachmark in the middle of the window welcomes the researcher to VISTA, says
  what VISTA is and that the tour takes about nine minutes, and offers to start
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
files, the Skill Hub and chat), it SHALL first show a coachmark that describes the screen as a
whole, with nothing dimmed, so the whole screen can be seen. It SHALL move on to that screen's
specific controls only on **Next**. For a step that starts at the navigation entry, the
overview SHALL come once the researcher has reached the screen.

#### Scenario: Arriving at knowledge bases

- **WHEN** a researcher on the knowledge bases step clicks the knowledge bases navigation entry
- **THEN** a coachmark describes the knowledge bases screen, and nothing on it is dimmed
- **AND** on **Next**, the coachmark points at OLCF User Documentation

#### Scenario: Arriving in chat

- **WHEN** opening a project brings the researcher to the chat
- **THEN** a coachmark describes the chat screen before pointing at the composer

#### Scenario: Arriving at the Skill Hub

- **WHEN** a researcher on the collaboration step clicks the Skill Hub navigation entry
- **THEN** a coachmark describes the Skill Hub, and nothing on it is dimmed

## ADDED Requirements

### Requirement: Collaboration step

The collaboration step SHALL show these coachmarks in order, with the approved storyboard's
wording:
1. It SHALL point at the Skill Hub navigation entry and wait for the researcher to open it.
2. It SHALL describe the Skill Hub.
3. In the middle of the window, it SHALL say that projects, skills and knowledge bases stay on
   this computer, reach the server only when published, and arrive only when imported.
4. It SHALL point at the Skill Hub's team filter and say that everything made starts Private,
   on this computer only, that once published it is in one team and stays there, what a team
   and Public mean, and that each item shows its team.
5. It SHALL point at an example shared skill and its Import.
6. It SHALL bring the researcher to the Skills page and point at an example private skill's
   **Publish…**, saying that something private gets its team on its first publish and stays in
   it, and that **Publish changes** then sends each new version there.
7. It SHALL open Settings › Collaboration and point at where to sign in. On VISTA's default
   server it SHALL add "No access yet? Reach out to the VISTA team." An installation already
   signed in SHALL instead be told which server it is signed in to, and that teams are made and
   joined with **Manage teams…**.
8. It SHALL point at the Collaboration switch and say that turning it off stops VISTA contacting
   the server and hides the Skill Hub, team badges and sharing buttons.

Coachmarks 2 to 8 SHALL move on with **Next**. The sign-in field and the switch SHALL stay usable
while they are explained. The step SHALL NOT require signing in or changing the switch. Leaving
the step SHALL close Settings.

The step SHALL be skipped while collaboration is off, whenever it is reached. Turning
collaboration off during the step SHALL end it, and the tour SHALL go on to the first question.
The finish coachmark SHALL say the researcher "learned how sharing works".

#### Scenario: A researcher who is not signed in

- **GIVEN** a fresh install on the default server, not signed in
- **WHEN** the researcher goes through the collaboration step with **Next**
- **THEN** every coachmark is shown, coachmark 7 includes the VISTA team sentence, and the tour
  goes on to the first question without anything being signed in or published

#### Scenario: Collaboration off

- **GIVEN** collaboration is turned off
- **WHEN** the tour finishes the HPC step
- **THEN** it goes straight to the first question

#### Scenario: Turning it off during the step

- **WHEN** the researcher turns the switch off at the switch's coachmark
- **THEN** Settings closes and the tour goes on to the first question

#### Scenario: Already signed in

- **GIVEN** an installation signed in to the default server
- **WHEN** the step reaches Settings › Collaboration
- **THEN** the coachmark says it is signed in to that server, names Manage teams…, and leaves
  out the VISTA team sentence

### Requirement: Collaboration step examples

While the collaboration step is shown, and only then, VISTA SHALL show tour-only examples in the
real components, each marked "Example", whatever the researcher's collaboration state:
- in the Skill Hub's team filter: Public and "Example team", in place of the real list, even
  where the filter is normally hidden;
- in the Skill Hub: `plot-results` (Public) and `batch-job-submit` (Example team), each with its
  team badge, in place of the page's own content;
- in the Skills page, added to the real list: `csv-cleanup` (private, with **Publish…**) and
  `run-summary` (Example team, with **Publish changes**).

Choosing an example team in the filter SHALL only narrow the example cards. Using an example's
Import, Publish… or Publish changes SHALL do nothing, and Publish… SHALL NOT open the team
picker. No example SHALL be sent to the backend, saved, or visible to the agent. Every example
SHALL be gone once the step ends, by completing, skipping or exiting.

#### Scenario: Examples while signed out

- **GIVEN** an installation that is not signed in, whose team filter and badges are normally
  hidden
- **WHEN** the step points at the team filter
- **THEN** the filter lists All teams, Public and Example team, marked "Example", and the example
  cards show their team badges

#### Scenario: Examples never reach the server

- **WHEN** a researcher clicks an example's Import, Publish… or Publish changes during the step
- **THEN** no request is sent to the backend, no team picker opens, and nothing changes

#### Scenario: Examples are gone afterwards

- **WHEN** the collaboration step ends
- **THEN** the team filter, team badges, Skill Hub and Skills page show only the installation's
  real state
