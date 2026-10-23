## MODIFIED Requirements

### Requirement: Save as skill from the report
"Save as skill" in the report modal SHALL draft a skill from the conversation's
message history, steered by the current (possibly edited) report text as the
drafting hint, and SHALL open the existing skill editor with that draft. The
editor SHALL save the skill to this computer only; publishing it is done from
the Skills page (`skill-collaboration`).

#### Scenario: Skill drafted from report
- **WHEN** the user clicks "Save as skill" in the report modal
- **THEN** the skill editor opens with a draft produced from the conversation using the report as the hint

#### Scenario: Saving does not publish
- **WHEN** the user saves the drafted skill
- **THEN** it is saved to this computer, loaded into the active project, and not published
