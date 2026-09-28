# model-picker Specification

## Purpose
Lets a researcher see which models their configured inference endpoint actually offers, and
choose one, from inside the chat window rather than by typing a model name from memory.

## Requirements

### Requirement: Chat window shows available models

The chat window SHALL let a signed-in researcher see the models currently available through
their configured inference endpoint, without leaving the chat view.

#### Scenario: Opening the picker

- **WHEN** a researcher with an inference credential configured opens the model picker in the
  chat window
- **THEN** the picker lists the model identifiers currently offered by their configured
  inference endpoint

### Requirement: Selecting a model sets the researcher's active model

Selecting a model in the picker SHALL update the model used for that researcher's subsequent
chat turns, as a standing preference rather than a one-time choice for a single message.

#### Scenario: Selection persists across turns

- **WHEN** a researcher selects a model from the picker
- **THEN** later chat turns for that researcher use the selected model until they change it
  again, including after starting a new conversation

#### Scenario: Selection is visible wherever the model is configured

- **WHEN** a researcher selects a model from the chat-window picker
- **THEN** the same selection is reflected wherever else their active model is shown or edited

### Requirement: Missing credential is a reported state, not a broken picker

When no inference credential is configured, the picker SHALL report that as a named condition
and say how to resolve it, consistent with how a missing credential is already reported
elsewhere in VISTA, rather than showing an empty or erroring list.

#### Scenario: No credential configured

- **WHEN** a researcher with no inference credential configured opens the model picker
- **THEN** the picker states that a credential is required and where to add one, and does not
  present a stale or fabricated model list

### Requirement: Discovery degrades for endpoints that don't support it

For a configured inference endpoint that does not support listing its available models, the
system SHALL NOT claim to offer a live, discovered list, and SHALL let the researcher set their
model by name instead.

#### Scenario: Endpoint without discovery support

- **WHEN** a researcher's configured inference endpoint does not support listing available
  models
- **THEN** the researcher can still set which model to use by entering its name directly, and
  the picker does not present a list as if it were live and complete
