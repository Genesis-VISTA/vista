## MODIFIED Requirements

### Requirement: Selecting a model sets the researcher's active model

Selecting a model in the picker while no chat is open, as in the conversation list, SHALL
set the researcher's default model as a standing preference rather than a one-time choice
for a single message. Every later model call made for that researcher SHALL use the
default unless a chat or debate it belongs to sets its own model: chat turns, campaigns,
debates, report and skill authoring, and citation extraction. Selecting a model while a
chat is open SHALL NOT change the default. A turn already running SHALL finish on the model
it started with. The picker SHALL be the only place in the interface where the model is
chosen; Settings SHALL NOT offer a model field.

The picker's label SHALL always show the model in effect: after a selection, after Settings
changes the provider, and after the chosen model is cleared, without a reload.

#### Scenario: Selection persists across turns

- **WHEN** a researcher selects a model from the picker in the conversation list
- **THEN** later chat turns for that researcher use the selected model until they change it
  again, both in chats without their own model and in a new chat

#### Scenario: Selecting inside a chat leaves the default alone

- **WHEN** a researcher selects a model from the picker while a chat is open, then returns
  to the conversation list
- **THEN** the picker in the conversation list still shows the earlier default

#### Scenario: Selection is visible in Settings

- **WHEN** a researcher selects a model from the chat-window picker and then opens Settings
- **THEN** Settings offers no model field that could show a different model, and the
  picker still shows the selected model, without its `openai:` prefix

#### Scenario: Label follows a provider change

- **WHEN** a researcher who chose a model switches provider in Settings
- **THEN** the picker's label stops showing that model without a reload

## ADDED Requirements

### Requirement: A chat may set its own model

Selecting a model in the picker while a chat is open SHALL set that chat's own model and
SHALL affect no other chat. A chat without its own model SHALL use the researcher's
default as it is when each turn starts, including a default changed after the chat was
created. A new chat SHALL start without its own model. While a chat has its own model, the
picker SHALL show it marked as this chat's, and SHALL offer to use the default instead,
naming it; choosing that SHALL clear the chat's own model. A chat's own model SHALL be a
model name sent to the researcher's current provider, never a provider of its own.

#### Scenario: Override applies to one chat only

- **GIVEN** a researcher whose default is model A, with chats 1 and 2
- **WHEN** they select model B while chat 1 is open
- **THEN** chat 1's next turn uses B, and chat 2's next turn uses A

#### Scenario: A chat without its own model follows the default

- **GIVEN** chat 1 without its own model, created while the default was A
- **WHEN** the researcher changes the default to B and sends a message in chat 1
- **THEN** the turn uses B

#### Scenario: Label shows the chat's own model

- **WHEN** a researcher opens a chat whose own model is B while the default is A
- **THEN** the picker reads B marked as this chat's, and its menu offers "Use default (A)"

#### Scenario: Clearing a chat's model

- **WHEN** the researcher chooses "Use default (A)" in chat 1
- **THEN** chat 1's label reads "Default (A)", and its later turns follow the default

#### Scenario: A chat's model on a provider that does not serve it

- **GIVEN** chat 1 whose own model is B, after the researcher switched to a provider that
  does not list B
- **WHEN** they open chat 1
- **THEN** the picker shows B marked as not listed by the provider, and a turn the provider
  refuses reports the error naming B rather than falling back to another model

#### Scenario: A chat's model satisfies sending without a default

- **GIVEN** a researcher on a provider with no default and no default chosen
- **WHEN** they send a message in a chat that has its own model
- **THEN** the turn starts on the chat's model

### Requirement: Work launched from a chat uses the chat's model

Report drafting, skill drafting and campaigns started from a chat SHALL use the model in
effect for that chat when they start. Work that runs in the background SHALL keep that
model until it finishes, even if the chat's model or the default changes meanwhile.
Citation extraction is started by indexing a knowledge base, not by a chat, and SHALL use
the default.

#### Scenario: Report from an overridden chat

- **GIVEN** chat 1 whose own model is B while the default is A
- **WHEN** the researcher generates a report from chat 1
- **THEN** the report is drafted by B

#### Scenario: Campaign keeps its launch model

- **GIVEN** a campaign launched from chat 1 while chat 1's model was B
- **WHEN** the researcher changes chat 1's model to C while the campaign is still running
- **THEN** the campaign's remaining steps, including those resumed by the background job
  monitor, use B
