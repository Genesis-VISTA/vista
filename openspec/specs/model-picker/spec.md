# model-picker Specification

## Purpose
Lets a researcher see which models their configured inference endpoint actually offers, and
choose one, from inside the chat window rather than by typing a model name from memory.

## Requirements

### Requirement: Chat window shows available models

The chat window SHALL let a signed-in researcher see the models currently available through
their configured inference endpoint, without leaving the chat view. The list SHALL be fetched
for the active project, which the researcher must be able to access, when the chat view loads
and whenever the active project changes; the project is an access check, not a filter.

#### Scenario: Opening the picker

- **WHEN** a researcher with an inference credential, whose active model is reached through
  the configured OpenAI-compatible endpoint, opens the model picker in the chat window
- **THEN** the picker lists, sorted by identifier, the model identifiers that endpoint
  reported, and marks the researcher's current model

### Requirement: Selecting a model sets the researcher's active model

Selecting a model in the picker SHALL update the researcher's active model as a standing
preference rather than a one-time choice for a single message. Every later model call made
for that researcher SHALL use it, including chat turns, campaigns, debates, report and skill
authoring, and citation extraction. A turn already running SHALL finish on the model it
started with.

#### Scenario: Selection persists across turns

- **WHEN** a researcher selects a model from the picker
- **THEN** later chat turns for that researcher use the selected model until they change it
  again, both in the open conversation and after starting a new one

#### Scenario: Selection is visible in Settings

- **WHEN** a researcher selects a model from the chat-window picker
- **THEN** the Model field in Settings shows the same model, without its `openai:` prefix,
  the next time Settings is opened

### Requirement: Missing credential is a reported state, not a broken picker

When the researcher's active model is reached through the configured endpoint and no
inference credential is configured, or the endpoint rejects the one that is, the picker SHALL
report that as a named condition and say how to resolve it, in the same words a chat turn
uses, rather than showing an empty or erroring list.

#### Scenario: No credential configured

- **WHEN** a researcher with no inference credential configured, whose active model is
  reached through the configured endpoint, opens the model picker
- **THEN** the picker states that a credential is required and where to add one, and does not
  present a stale or fabricated model list

#### Scenario: Credential rejected by the endpoint

- **WHEN** the configured endpoint refuses the researcher's credential while listing models
- **THEN** the picker shows the same rejected-credential guidance a chat turn would, naming
  Settings, and shows no list

### Requirement: Discovery is offered only for the configured endpoint

The system SHALL attempt model discovery only when the researcher's active model resolves to
the configured OpenAI-compatible endpoint. For any other active model, such as another
provider or the test stub, it SHALL report discovery as unavailable without contacting
anything, and SHALL let the researcher set their model by name in Settings. A listing that
fails SHALL be shown as an error, never as a list.

#### Scenario: Active model on a provider without discovery

- **WHEN** the researcher's active model uses a provider other than the configured
  OpenAI-compatible endpoint
- **THEN** the picker says that the endpoint does not report its models, points to the Model
  field in Settings, and shows no list

#### Scenario: Listing fails

- **WHEN** the configured endpoint's model listing fails for a reason other than a rejected
  credential, such as being unreachable or answering with an error
- **THEN** the picker shows an error in place of a list
