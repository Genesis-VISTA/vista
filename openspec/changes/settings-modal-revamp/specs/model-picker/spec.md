## MODIFIED Requirements

### Requirement: Chat window shows available models

The chat window SHALL let a signed-in researcher see the models currently available through
their chosen inference provider, without leaving the chat view. The list SHALL be fetched
for the active project, which the researcher must be able to access, when the chat view
loads, whenever the active project changes, whenever the picker is opened, and whenever the
researcher's provider, endpoint or key changes; the project is an access check, not a
filter.

#### Scenario: Opening the picker

- **WHEN** a researcher with an inference credential opens the model picker in the chat
  window
- **THEN** the picker lists, sorted by identifier, the model identifiers their provider
  reported, and marks the researcher's current model

#### Scenario: Provider changed in Settings

- **WHEN** a researcher changes their provider, endpoint or key in Settings and then opens
  the picker, without reloading
- **THEN** the picker lists the models of the provider now in effect, not the previous one

### Requirement: Selecting a model sets the researcher's active model

Selecting a model in the picker SHALL update the researcher's active model as a standing
preference rather than a one-time choice for a single message. Every later model call made
for that researcher SHALL use it, including chat turns, campaigns, debates, report and skill
authoring, and citation extraction. A turn already running SHALL finish on the model it
started with. The picker SHALL be the only place in the interface where the model is
chosen; Settings SHALL NOT offer a model field.

The picker's label SHALL always show the model in effect: after a selection, after Settings
changes the provider, and after the chosen model is cleared, without a reload.

#### Scenario: Selection persists across turns

- **WHEN** a researcher selects a model from the picker
- **THEN** later chat turns for that researcher use the selected model until they change it
  again, both in the open conversation and after starting a new one

#### Scenario: Selection is visible in Settings

- **WHEN** a researcher selects a model from the chat-window picker and then opens Settings
- **THEN** Settings offers no model field that could show a different model, and the
  picker still shows the selected model, without its `openai:` prefix

#### Scenario: Label follows a provider change

- **WHEN** a researcher who chose a model switches provider in Settings
- **THEN** the picker's label stops showing that model without a reload

### Requirement: Discovery is offered only for the configured endpoint

The system SHALL attempt model discovery whenever the researcher's active model is reached
through their chosen provider. For an active model that is not, such as the test stub or a
model the installation's own configuration routes elsewhere, it SHALL report discovery as
unavailable without contacting anything, and the researcher SHALL still be able to type a
model name in the picker. A listing that fails SHALL be shown as an error, never as a list.

#### Scenario: Active model on a provider without discovery

- **WHEN** the researcher's active model is not reached through their chosen provider
- **THEN** the picker says that the models cannot be listed, shows no list, and still
  offers to use another model by name

#### Scenario: Listing fails

- **WHEN** the provider's model listing fails for a reason other than a rejected
  credential, such as being unreachable or answering with an error
- **THEN** the picker shows an error in place of a list, and still offers to use another
  model by name

## ADDED Requirements

### Requirement: A model can be chosen by name

The picker SHALL always offer to use a model that is not in its list, by typing its name.
A typed name SHALL be sent to the chosen provider exactly as typed, including any colons in
it, and SHALL NOT be read as naming a different provider.

#### Scenario: Typing an unlisted model

- **WHEN** a researcher types `anthropic.claude-sonnet-v1:0` as another model
- **THEN** that is the active model, and chat turns send exactly that name to the chosen
  provider

### Requirement: An unlisted active model is marked

When the provider's listing succeeds and does not include the researcher's active model,
the picker SHALL keep that model active and SHALL mark it as not listed by the provider.

#### Scenario: Provider retired a model

- **WHEN** the researcher's chosen model is no longer in their provider's list
- **THEN** the picker's label still shows it, marked as not listed by the provider

### Requirement: The default is named

When the researcher has not chosen a model and their provider has a default, the picker's
label SHALL name it, as "Default (<model>)".

#### Scenario: Fresh install on i2

- **WHEN** a researcher on AmSC i2 has never chosen a model
- **THEN** the picker reads "Default (claude-sonnet)"

### Requirement: Sending without a model

When the researcher has not chosen a model and their provider has no default, sending a
message SHALL NOT start a turn. The message SHALL stay in the composer, the picker SHALL
open, and it SHALL say a model must be chosen first.

#### Scenario: First message on MAG

- **WHEN** a researcher on AmSC MAG with no model chosen sends a message
- **THEN** no turn starts, their message is still in the composer, and the picker opens
  asking them to choose a model
