## MODIFIED Requirements

### Requirement: Provider choice

The Agent section of Settings SHALL offer an inference provider choice with exactly these
options, in this order: AmSC MAG, OLCF Inference, and Custom. The choice SHALL persist per
researcher and take effect on the next request without a restart.

For AmSC MAG and OLCF Inference, VISTA SHALL use that provider's endpoint without showing it
or asking for it. Only Custom SHALL show an endpoint field. Each option SHALL show its own
API key field; for AmSC MAG the field SHALL accept a project access token, and for OLCF
Inference an S3M project access token with access to the Inference Service, kept apart from
the Odo and Frontier S3M tokens.

No option, label, link or request SHALL name or reach the retired AmSC i2 endpoint
(`api.i2-core.american-science-cloud.org`).

#### Scenario: Choosing MAG

- **WHEN** a researcher chooses AmSC MAG and enters a project access token
- **THEN** the next chat turn goes to the MAG endpoint with that token, and no endpoint
  field is shown

#### Scenario: Choosing OLCF Inference

- **WHEN** a researcher chooses OLCF Inference and enters an S3M token
- **THEN** the next chat turn goes to the OLCF Inference endpoint with that token, no
  endpoint field is shown, and the researcher's Odo and Frontier S3M tokens are unchanged

#### Scenario: Choosing Custom

- **WHEN** a researcher chooses Custom
- **THEN** an endpoint field and a key field are shown, and the next turn uses that
  endpoint and key

#### Scenario: i2 is not offered

- **WHEN** a researcher opens the provider choice
- **THEN** AmSC i2 is not among the options and no link to the i2-core API is shown

### Requirement: A credential per provider

VISTA SHALL keep a separate API key for each provider option, each stored encrypted at
rest. Switching provider SHALL NOT change or clear any provider's key, and the key used for
a request SHALL be the key of the chosen provider. A Custom endpoint SHALL be kept when
switching to another provider and back.

A key saved before provider choice existed SHALL NOT be used as any provider's key. It SHALL
be used only for an endpoint set in the installation's configuration.

#### Scenario: Trying MAG and going back

- **WHEN** a researcher with an OLCF Inference token switches to AmSC MAG, enters a MAG
  token, then switches back to OLCF Inference
- **THEN** chat uses the original OLCF token, and switching to MAG again uses the MAG token
  without re-entering it

#### Scenario: Existing researcher

- **WHEN** a researcher whose only saved key was saved before provider choice existed, or
  for AmSC i2, starts VISTA with no endpoint set in its configuration
- **THEN** AmSC MAG is in effect with no key, chat asks for a MAG project access token, and
  the old key is not sent to MAG

#### Scenario: Keys at rest

- **WHEN** keys for more than one provider have been stored
- **THEN** none is readable in plaintext from the application's database file

### Requirement: Default model per provider

Each provider option SHALL have its own default model, possibly none. AmSC MAG's default
SHALL be `claude-sonnet` and OLCF Inference's `gpt-oss-120b`; Custom SHALL have none. When
the researcher has not chosen a model, requests SHALL use the chosen provider's default.
Changing provider SHALL clear the researcher's chosen model.

#### Scenario: No model chosen on MAG

- **WHEN** a researcher on AmSC MAG has never chosen a model
- **THEN** chat uses `claude-sonnet`

#### Scenario: No model chosen on i2

- **WHEN** a researcher who was on AmSC i2 and never chose a model starts VISTA
- **THEN** chat uses AmSC MAG's `claude-sonnet`

#### Scenario: No model chosen on OLCF Inference

- **WHEN** a researcher switches to OLCF Inference and has not chosen a model there
- **THEN** chat uses `gpt-oss-120b`

#### Scenario: Switching provider clears the model

- **WHEN** a researcher who chose a model on AmSC MAG switches to Custom
- **THEN** no model is chosen, and Custom's lack of a default is reported rather than the
  MAG model being sent to the custom endpoint

## ADDED Requirements

### Requirement: A saved i2 choice moves to the default

When VISTA starts with a saved provider choice of AmSC i2, it SHALL clear that choice and the
model chosen with it, so the researcher is on the default provider with its default model. A
campaign run recorded on AmSC i2 SHALL likewise follow the default provider and model. No
other saved setting or key SHALL change.

#### Scenario: Researcher was on i2

- **WHEN** a researcher who had chosen AmSC i2 and a model on it starts the new VISTA
- **THEN** Settings shows AmSC MAG with its default model, and their other providers' keys
  and their Custom endpoint are unchanged

#### Scenario: Campaign run recorded on i2

- **WHEN** a campaign run recorded on AmSC i2 is rebuilt in the background
- **THEN** it runs on the default provider and model, and loading it does not fail
