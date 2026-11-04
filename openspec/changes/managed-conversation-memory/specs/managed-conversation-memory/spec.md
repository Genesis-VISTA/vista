## Purpose

Keep long VISTA conversations useful without sending their complete history to the model on
every turn, while preserving the complete transcript as the durable record.

## ADDED Requirements

### Requirement: Managed memory is configured per conversation

Each conversation SHALL expose a managed-memory toggle and a context size of `automatic`,
`compact`, or `extended` through the UI and backend API. New conversations SHALL default to
managed memory on with `automatic` context size.

#### Scenario: Settings survive reopening

- **WHEN** a researcher changes a conversation's memory setting and reopens it
- **THEN** that conversation returns the saved setting
- **AND** another conversation is unchanged

### Requirement: The complete transcript remains the source of truth

VISTA SHALL continue to store every completed model message independently of the bounded
context supplied to the agent. Turning managed memory off SHALL NOT delete the transcript,
summary, or archive.

#### Scenario: Managed memory is off

- **GIVEN** a conversation with earlier turns
- **WHEN** its managed memory is off and the researcher sends a prompt
- **THEN** the agent receives the new prompt without earlier turns
- **AND** the new turn is appended to the complete transcript

### Requirement: Long conversations are compressed at complete-turn boundaries

When managed memory is on and the selected context threshold is exceeded, VISTA SHALL retain
recent turns exactly, archive only complete older user turns, and update a rolling summary of
objectives, constraints, decisions, corrections, results, artifacts, and open tasks. It SHALL
omit low-value repetition from the active summary.

#### Scenario: Automatic compression

- **GIVEN** a conversation beyond its selected threshold
- **WHEN** a turn completes
- **THEN** older complete turns are summarized and archived
- **AND** recent turns remain unchanged
- **AND** the complete transcript remains unchanged

### Requirement: Older details are retrieved within the conversation only

For a managed conversation with archived turns, VISTA SHALL assemble model context from the
rolling summary, relevant archived turns, and recent exact turns. Retrieval SHALL be restricted
to the current conversation and SHALL degrade to summary plus local archive search if the vector
index is unavailable.

#### Scenario: Relevant older result

- **GIVEN** an archived result in one conversation
- **WHEN** a later prompt in that conversation refers to the result
- **THEN** the bounded context includes the relevant archived detail
- **AND** it includes no archived detail from another conversation
