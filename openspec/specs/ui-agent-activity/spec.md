# ui-agent-activity Specification

## Purpose
Define how an agent run reads in the chat workspace, so a scientist who is not
reading logs can tell what the agent is doing, what it produced, and where the
result came from.

## Requirements

### Requirement: One live status line in the thread

While a run is in progress the conversation thread SHALL show exactly one
status line (`role=status`), updating in place, and SHALL remove it when the
run's stream ends.

#### Scenario: Status updates rather than accumulates

- **WHEN** an agent run invokes several tools in sequence
- **THEN** the thread SHALL show a single status line with an animated indicator, showing the running tool's label while a tool runs and "Working on it" otherwise
- **AND** completed steps MUST NOT accumulate as separate bubbles in the thread

#### Scenario: Status line clears on completion

- **WHEN** the run's stream ends
- **THEN** the status line SHALL disappear
- **AND** a completed run SHALL leave the user turn and the final answer in the thread
- **AND** a stopped, failed or interrupted run SHALL also leave a one-line note saying so

#### Scenario: Readable tool labels with a fallback

- **WHEN** the status line describes a step whose tool has a known label
- **THEN** it SHALL show that label, such as "Searching the literature"
- **AND** a namespaced tool name SHALL be matched on the part after its last `__`
- **AND** **WHEN** the tool is not in the label map, it SHALL show the raw tool name rather than nothing

### Requirement: Tabbed workspace column

The workspace column beside the conversation SHALL present Artifacts, Activity,
and Jobs as tabs rather than stacked panes.

#### Scenario: Default tab

- **WHEN** a user opens the chat route
- **THEN** the workspace column SHALL render as tabs with Artifacts selected
- **AND** the selected tab SHALL be kept when the user opens another conversation

#### Scenario: Jobs is conditional

- **WHEN** the conversation has no campaign
- **THEN** the Jobs tab SHALL NOT be rendered
- **AND** **WHEN** a campaign exists, the Jobs tab SHALL appear and show its state

### Requirement: Activity view

The Activity tab SHALL list the conversation's intermediate steps in order, with
the raw log text available behind a toggle.

#### Scenario: Structured steps, not a terminal

- **WHEN** a run executes tool calls
- **THEN** the Activity tab SHALL list each tool call as a step naming its tool, along with any assistant text from before the final answer, and SHALL mark the step in progress as live
- **AND** the raw log text SHALL be reachable through an explicit toggle rather than being the default presentation

#### Scenario: Activity survives navigation within the session

- **WHEN** a user switches to another tab and back during a run
- **THEN** the steps already shown SHALL still be present

#### Scenario: Persisted steps after reload

- **WHEN** a user reloads a finished conversation
- **THEN** the Activity tab SHALL be built from the persisted conversation record
- **AND** it SHALL show no less than what the conversation persisted before this change
- **AND** the raw log SHALL say that it is live-only and empty after a reload

### Requirement: No agent-bypassing shortcuts

The chat surface SHALL NOT offer controls that run tools outside the agent loop
and its approval and code-scanning gates.

#### Scenario: Salt quick-actions removed

- **WHEN** a user opens the chat page
- **THEN** the "Analyze Salt" and "Predict Salt" quick-action buttons SHALL NOT be present
- **AND** no remaining control SHALL construct a shell command from user input outside the agent's approval path

#### Scenario: Result panels keep their data source

- **WHEN** an agent run produces tool output that populates the prediction summary or references panels
- **THEN** its text SHALL be taken from the tool-return events of the agent stream into the latest result, which feeds those panels
- **AND** those panels MUST NOT depend on a removed control to render

### Requirement: Suggested openings

An empty conversation SHALL offer suggestions that prefill the composer.

#### Scenario: Suggestions prefill rather than send

- **WHEN** a user opens a conversation with no messages and selects a suggestion
- **THEN** the composer SHALL be populated with that text
- **AND** the message MUST NOT be sent until the user submits it
