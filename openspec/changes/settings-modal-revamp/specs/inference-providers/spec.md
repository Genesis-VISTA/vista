## Purpose

Lets a researcher choose which inference provider VISTA talks to from a short list of known
gateways, or a custom endpoint, with a separate credential kept for each.

## ADDED Requirements

### Requirement: Provider choice

The Agent section of Settings SHALL offer an inference provider choice with exactly these
options: AmSC i2, AmSC MAG, and Custom. The choice SHALL persist per researcher and take
effect on the next request without a restart.

For AmSC i2 and AmSC MAG, VISTA SHALL use that provider's endpoint without showing it or
asking for it. Only Custom SHALL show an endpoint field. Each option SHALL show its own API
key field; for AmSC MAG the field SHALL accept a project access token.

#### Scenario: Choosing MAG

- **WHEN** a researcher chooses AmSC MAG and enters a project access token
- **THEN** the next chat turn goes to the MAG endpoint with that token, and no endpoint
  field is shown

#### Scenario: Choosing Custom

- **WHEN** a researcher chooses Custom
- **THEN** an endpoint field and a key field are shown, and the next turn uses that
  endpoint and key

### Requirement: A credential per provider

VISTA SHALL keep a separate API key for each provider option, each stored encrypted at
rest. Switching provider SHALL NOT change or clear any provider's key, and the key used for
a request SHALL be the key of the chosen provider. A Custom endpoint SHALL be kept when
switching to another provider and back.

A key saved before provider choice existed SHALL be the AmSC i2 key.

#### Scenario: Trying MAG and going back

- **WHEN** a researcher with an i2 key switches to AmSC MAG, enters a MAG token, then
  switches back to AmSC i2
- **THEN** chat uses the original i2 key, and switching to MAG again uses the MAG token
  without re-entering it

#### Scenario: Existing researcher

- **WHEN** a researcher who saved an API key before this change opens Settings
- **THEN** AmSC i2 is selected and their key is its key

#### Scenario: Keys at rest

- **WHEN** keys for more than one provider have been stored
- **THEN** none is readable in plaintext from the application's database file

### Requirement: Default model per provider

Each provider option SHALL have its own default model, possibly none. AmSC i2's default
SHALL be `claude-sonnet`; AmSC MAG and Custom SHALL have none. When the researcher has not
chosen a model, requests SHALL use the chosen provider's default. Changing provider SHALL
clear the researcher's chosen model.

#### Scenario: No model chosen on i2

- **WHEN** a researcher on AmSC i2 has never chosen a model
- **THEN** chat uses `claude-sonnet`

#### Scenario: Switching provider clears the model

- **WHEN** a researcher who chose a model on AmSC i2 switches to AmSC MAG
- **THEN** no model is chosen, and MAG's lack of a default is reported rather than the i2
  model being sent to MAG

### Requirement: Providers are described to the interface

The backend SHALL report to the interface the provider options, each option's default
model, which option is in effect for the researcher, and whether that option comes from
the researcher's choice or from the installation's own configuration. The interface SHALL
build the provider choice and the picker's default label from that report rather than
from values of its own.

#### Scenario: Labels follow the backend

- **WHEN** a provider's default model is changed in the backend's provider definitions
- **THEN** the picker's default label shows the new model without any interface change

### Requirement: Citation extraction follows the chosen provider

Citation extraction during knowledge-base indexing SHALL use the researcher's chosen
provider, its key, and the model chat would use. When the chosen provider has no model,
indexing SHALL complete without citation extraction and SHALL say so, as it does when no
key is configured.

#### Scenario: Indexing on MAG with no model

- **WHEN** a researcher on AmSC MAG with no model chosen indexes a paper
- **THEN** the text is indexed, citations are not extracted, and the researcher is told
  why

### Requirement: Model listing tolerates a versioned endpoint

Listing a provider's models SHALL reach the same models whether that provider's endpoint is
written with or without a trailing `/v1`.

#### Scenario: Endpoint ending in /v1

- **WHEN** the chosen provider's endpoint ends in `/v1`
- **THEN** the picker lists that provider's models rather than an error
