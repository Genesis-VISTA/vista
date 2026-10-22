# model-picker Specification

## Purpose
Lets a researcher see which models their configured inference endpoint actually offers, and
choose one, from inside the chat window rather than by typing a model name from memory.

## Requirements

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

### Requirement: The model list can be searched

When the provider lists at least one model, the picker SHALL open on a search box that
has focus. Typing SHALL narrow the list to the identifiers that contain the text,
ignoring case, keeping their sorted order. Enter SHALL choose the first model still
listed. When nothing matches, the picker SHALL say so. The search SHALL be cleared
when the picker closes.

#### Scenario: Narrowing a long list

- **WHEN** a researcher on AmSC MAG opens the picker and types part of a model's name
- **THEN** only the models whose identifiers contain that text, in any case, are listed

#### Scenario: Choosing with Enter

- **WHEN** a researcher types in the search box and presses Enter
- **THEN** the first model still listed becomes their active model and the picker closes

#### Scenario: Nothing matches

- **WHEN** the search text is in no listed identifier
- **THEN** the picker lists no models, says that none match, and still offers to use
  another model by name

#### Scenario: Reopening

- **WHEN** a researcher closes the picker after searching and opens it again
- **THEN** the search box is empty and every listed model is shown

### Requirement: Full identifiers on hover

Each listed model, and the picker's label, SHALL show the model's full identifier on
hover, so an identifier cut off for width can still be read.

#### Scenario: A long variant name

- **WHEN** a researcher hovers over a listed model whose identifier is too long for the
  menu
- **THEN** its full identifier is shown

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
model until it finishes, even if the chat's model or the default changes meanwhile. A
campaign whose researcher has since switched provider SHALL use their current default,
since a model name means nothing on another provider. Citation extraction is started by indexing a knowledge base, not by a chat, and SHALL use
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

#### Scenario: Campaign after a provider switch

- **GIVEN** a campaign launched on provider P's model B
- **WHEN** the researcher switches to provider Q while the campaign is still running
- **THEN** the campaign's remaining steps use Q's current default, and the switch is logged
