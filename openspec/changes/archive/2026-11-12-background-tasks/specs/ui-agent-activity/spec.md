## MODIFIED Requirements

### Requirement: Tabbed workspace column

The workspace column beside the conversation SHALL present Artifacts, Activity,
Background, and Jobs as tabs rather than stacked panes.

#### Scenario: Default tab

- **WHEN** a user opens the chat route
- **THEN** the workspace column SHALL render as tabs with Artifacts selected
- **AND** the selected tab SHALL be kept when the user opens another conversation

#### Scenario: Jobs is conditional

- **WHEN** the conversation has no campaign
- **THEN** the Jobs tab SHALL NOT be rendered
- **AND** **WHEN** a campaign exists, the Jobs tab SHALL appear and show its state

#### Scenario: Background is conditional

- **WHEN** the conversation has never had a background task
- **THEN** the Background tab SHALL NOT be rendered
- **AND** **WHEN** the conversation has a task in any state, the Background tab SHALL appear

## ADDED Requirements

### Requirement: Background view

The Background tab SHALL list the open conversation's background tasks, running ones first,
and SHALL update while it is open without a reload. Each row SHALL show what the task is (the
command, or the job id and cluster), its state, and its runtime. A background command's row
SHALL link to its log. A running command or active watch SHALL have a stop control. A paused
watch SHALL say which cluster needs reconnecting. The tab's label SHALL carry the number of
running tasks when there are any.

#### Scenario: A running command

- **WHEN** the conversation has a background command running
- **THEN** the Background tab SHALL show a row with the command, a running state, a runtime
  that advances, a link to the log and a stop control

#### Scenario: Stopping from the tab

- **WHEN** a researcher activates a running task's stop control
- **THEN** the row SHALL change to stopped and its stop control SHALL disappear

#### Scenario: A paused watch

- **WHEN** a job watch is paused for lost cluster access
- **THEN** its row SHALL say the watch is paused and name the cluster to reconnect

#### Scenario: Other conversations' tasks

- **WHEN** another conversation in the same project has tasks running
- **THEN** the open conversation's Background tab SHALL NOT list them
