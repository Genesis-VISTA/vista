## Purpose

Define the chrome VISTA presents on every route: one shared top bar, a project
that travels with the user instead of being a mode entered from one page, and
consistent rules for what a route does when no project is selected.

## ADDED Requirements

### Requirement: Single application shell

Every user-facing route SHALL render the same navigation rail, and every route
except the Hypothesis Lab SHALL render the same top bar. No route other than the
Hypothesis Lab may define its own page-title chrome.

#### Scenario: Every route carries the shared bar

- **WHEN** a user visits chat, projects, datasets, skills, skill hub, or knowledge bases
- **THEN** each page SHALL render the shared top bar with the current page title
- **AND** the navigation rail SHALL carry the Genesis lockup while it is expanded
- **AND** every project-scoped page SHALL show the project switcher in the bar, while the projects page and the global knowledge-base view SHALL NOT
- **AND** no page SHALL render a second title row or a page-scoped toolbar duplicating controls the shared bar provides

#### Scenario: Knowledge bases drops its scoped toolbar

- **WHEN** a user opens knowledge bases
- **THEN** the page-scoped toolbar SHALL be gone
- **AND** its refresh and create actions SHALL be reachable from the shared bar or the page header
- **AND** both modes SHALL remain available, as `/knowledge-bases` (global) and `/knowledge-bases/project` (project-scoped)

### Requirement: Project is ambient context

The active project SHALL be switchable from every project-scoped route through a
control in the shared top bar.

#### Scenario: Switching project preserves the current route

- **WHEN** a user on the datasets page switches to a different project
- **THEN** the user SHALL remain on the datasets page, now scoped to the newly selected project
- **AND** the user MUST NOT be redirected to chat or to the project list

#### Scenario: Active project is visible everywhere

- **WHEN** a project is active
- **THEN** its name SHALL be shown in the top bar's switcher on every project-scoped route
- **AND** in the navigation rail's opened-project chip on every route while the rail is expanded

### Requirement: Routes that require a project

Routes that cannot render anything meaningful without a project SHALL either
redirect to project selection or show a shared selection prompt, and MUST NOT
present inert or misleading controls.

#### Scenario: Chat redirects and returns

- **WHEN** a user opens chat with no project selected
- **THEN** the app SHALL redirect to the project picker and remember chat as the intended destination
- **AND** on selecting a project the user SHALL land on chat

#### Scenario: Datasets and skills prompt in place

- **WHEN** a user opens datasets or skills with no project selected
- **THEN** each page SHALL render the same "pick a project first" panel
- **AND** selecting a project from that panel SHALL keep the user on the page they opened

#### Scenario: Hypothesis Lab without a project

- **WHEN** a user opens the Hypothesis Lab with no project selected
- **THEN** the page SHALL say that a project must be selected first, and render no debate controls

#### Scenario: Knowledge bases does not redirect

- **WHEN** a user opens `/knowledge-bases` or `/knowledge-bases/project` with no project selected
- **THEN** the global view SHALL render
- **AND** no redirect SHALL occur

#### Scenario: Project navigation is never inert

- **WHEN** a user views the navigation rail with no project selected
- **THEN** the opened-project entries SHALL be clickable
- **AND** no navigation entry SHALL claim a destination is "coming soon" when that destination exists

### Requirement: Navigation rail defaults and honesty

The navigation rail SHALL open expanded on first use, remember the user's
collapse choice afterwards, and contain no permanently disabled entries.

#### Scenario: First visit shows group headings

- **WHEN** a user loads the app for the first time
- **THEN** the rail SHALL be expanded, showing group headings and the active project chip

#### Scenario: No destination-less entries

- **WHEN** the rail renders
- **THEN** every entry SHALL navigate somewhere
- **AND** entries with no destination SHALL NOT be present

### Requirement: Project list empty state

The projects page SHALL render an explicit empty state when no projects exist.

#### Scenario: No projects yet

- **WHEN** a user reaches the projects page and no projects exist
- **THEN** the page SHALL explain what a project is and offer creation
- **AND** it SHALL NOT render an empty grid with no explanation
