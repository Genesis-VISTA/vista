## Purpose

Lets a researcher turn a chat conversation into a readable, editable report,
save it into their project where a later chat's agent can find it, and derive a
reusable skill from it.

## ADDED Requirements

### Requirement: Generate report entry point
The chat header SHALL offer a "Generate report" action in place of the former
"Save as skill" action whenever a conversation is open. The action SHALL be
disabled while the conversation has no messages. The header SHALL NOT offer a
separate Save-as-skill action.

#### Scenario: Button replaces Save as skill
- **WHEN** the user opens a conversation
- **THEN** the header shows "Generate report" and does not show "Save as skill"

#### Scenario: Empty conversation
- **WHEN** the open conversation has no messages
- **THEN** "Generate report" is disabled

### Requirement: Draft a report from the conversation
The system SHALL draft a report from the conversation's full message history
using the signed-in user's configured model, and SHALL persist nothing while
drafting. A draft SHALL contain a title, a filename slug suggestion, a one-line
summary, and a markdown body. The body SHALL begin with a readable summary
(goal, approach, results, next steps) followed by a "Record" section listing the
concrete values, file paths, job identifiers, and settings from the
conversation. Output files SHALL be referenced by their sandbox path
(`/mnt/data/output/...`) rather than copied. An optional free-text focus hint
SHALL steer the draft when provided.

#### Scenario: Draft opens in the report modal
- **WHEN** the user clicks "Generate report"
- **THEN** a report modal opens in a drafting state and then shows the draft's title and rendered body

#### Scenario: Draft has summary then record
- **WHEN** a draft is produced for a conversation that ran tools with concrete results
- **THEN** the body contains a readable summary followed by a "Record" section with those results

#### Scenario: Drafting does not persist
- **WHEN** a draft is produced
- **THEN** no report file is written to the project

#### Scenario: Drafting fails
- **WHEN** drafting fails (for example the model errors or no inference credential is configured)
- **THEN** the modal shows the error and offers no save actions for an empty draft

#### Scenario: Hermetic drafting
- **WHEN** the draft endpoint is called in tests with a fake model
- **THEN** it returns a structured draft without network access or API keys

### Requirement: Review and steer the report
The report modal SHALL render the report body as markdown and SHALL display
images referenced by `/mnt/data/output/...` or `/mnt/data/uploads/...` paths
inline, resolved through the project's existing file routes. The user SHALL be
able to switch to editing the markdown directly, and SHALL be able to
regenerate the draft with a focus hint. Regenerating SHALL replace the current
draft, including any hand edits.

#### Scenario: Linked plot renders inline
- **WHEN** the body references an image at `/mnt/data/output/plots/density.png`
- **THEN** the modal displays that image, loaded from the project's outputs route

#### Scenario: Hand edit is kept for saving
- **WHEN** the user edits the markdown and then saves
- **THEN** the saved report contains the edited text

#### Scenario: Regenerate with focus
- **WHEN** the user enters a focus hint and chooses "Regenerate with focus…"
- **THEN** a new draft is produced using that hint and replaces the current one

### Requirement: Save report to project
"Save to project" SHALL write the report as a markdown file under `reports/` in
the user's uploads for the current project, so that it appears in the Datasets
page's uploads list. The file SHALL begin with a YAML header containing
`title`, `summary`, `date` (ISO date), and `chat_session_id`, followed by the
body. The filename SHALL be derived from the slug and sanitized like any other
upload. The modal SHALL confirm the saved path.

#### Scenario: First save
- **WHEN** the user saves a report from a chat that has no saved report
- **THEN** a file `reports/<slug>.md` is created in the project uploads with the header and body

#### Scenario: Report visible on the Datasets page
- **WHEN** a report has been saved
- **THEN** the Datasets page's uploads list includes it under `reports/`

#### Scenario: Unsafe slug
- **WHEN** the slug contains path separators, `..`, or other unsafe characters
- **THEN** the file is still written inside `reports/` with a sanitized name

#### Scenario: Name collision with another chat's report
- **WHEN** a different chat already saved a report with the same slug
- **THEN** the new report is written under a unique name and the other report is untouched

### Requirement: One report per chat
Saving a report from a chat that already has a saved report SHALL replace that
report's contents in place, identified by the `chat_session_id` in its header,
and SHALL keep its existing filename even if the new slug differs. If that
report no longer exists, saving SHALL create a new file.

#### Scenario: Re-save replaces and keeps filename
- **WHEN** the user saves again from the same chat with a different title
- **THEN** the existing file is overwritten with the new header and body and its filename is unchanged

#### Scenario: Earlier report was deleted
- **WHEN** the user deleted the chat's report on the Datasets page and saves again
- **THEN** a new report file is created

### Requirement: Save as skill from the report
"Save as skill" in the report modal SHALL draft a skill from the conversation's
message history, steered by the current (possibly edited) report text as the
drafting hint, and SHALL open the existing skill editor with that draft. Skill
saving and publishing SHALL behave as before.

#### Scenario: Skill drafted from report
- **WHEN** the user clicks "Save as skill" in the report modal
- **THEN** the skill editor opens with a draft produced from the conversation using the report as the hint

### Requirement: Agent knows where uploads and reports live
The agent's base system prompt SHALL state that the user's uploaded files are
in `/mnt/data/uploads/` and that saved conversation reports are in
`/mnt/data/uploads/reports/`, and SHALL instruct the agent to look there when the
user refers to earlier work or a report. The prompt SHALL NOT list individual
reports.

#### Scenario: Pointer present in every chat
- **WHEN** a project agent's system prompt is built
- **THEN** it contains both paths and does not enumerate report files
