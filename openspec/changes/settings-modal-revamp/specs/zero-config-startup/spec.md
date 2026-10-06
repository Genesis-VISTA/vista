## MODIFIED Requirements

### Requirement: Default inference target

VISTA SHALL ship with the AmSC i2 provider and its default model in effect, so that an
access credential is the only outstanding value after installation. A researcher who has
never chosen a provider SHALL be on AmSC i2.

When the installation's own configuration sets the inference model or endpoint, and the
researcher has not chosen a provider, that configuration SHALL take effect, and the
interface SHALL show the provider as coming from the installation's configuration rather
than as AmSC i2. A provider the researcher chooses SHALL take precedence over it.

#### Scenario: Only a credential is outstanding

- **WHEN** a researcher installs VISTA and supplies nothing
- **THEN** AmSC i2 is selected with its default model, and the interface identifies the
  access credential as the single remaining requirement

#### Scenario: Endpoint set in the installation's configuration

- **WHEN** VISTA's configuration sets an inference endpoint other than i2's and the
  researcher has never chosen a provider
- **THEN** requests use that endpoint, and Settings shows the provider as Custom, from the
  installation's configuration

#### Scenario: The researcher's choice wins

- **WHEN** the configuration sets an endpoint and the researcher then chooses AmSC MAG
- **THEN** requests go to MAG
