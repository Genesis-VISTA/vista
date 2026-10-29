## MODIFIED Requirements

### Requirement: Default inference target

VISTA SHALL ship with the AmSC MAG provider and its default model in effect, so that an
access credential is the only outstanding value after installation. A researcher who has
never chosen a provider SHALL be on AmSC MAG.

When the installation's own configuration sets the inference model or endpoint, and the
researcher has not chosen a provider, that configuration SHALL take effect, and the
interface SHALL show the provider as coming from the installation's configuration rather
than as AmSC MAG. A provider the researcher chooses SHALL take precedence over it.

A configured endpoint on the retired AmSC i2 host (`api.i2-core.american-science-cloud.org`)
SHALL be treated as unset, with a warning in the backend log that names the setting to change.

#### Scenario: Only a credential is outstanding

- **WHEN** a researcher installs VISTA and supplies nothing
- **THEN** AmSC MAG is selected with its default model, and the interface identifies the
  access credential as the single remaining requirement

#### Scenario: Endpoint set in the installation's configuration

- **WHEN** VISTA's configuration sets an inference endpoint other than MAG's and the
  researcher has never chosen a provider
- **THEN** requests use that endpoint, and Settings shows the provider as Custom, from the
  installation's configuration

#### Scenario: Configuration still names i2

- **WHEN** VISTA's configuration sets the inference endpoint to the retired i2-core API and
  the researcher has never chosen a provider
- **THEN** AmSC MAG is in effect, no request goes to i2-core, and the backend log says
  the configured endpoint was ignored and which setting to change

#### Scenario: The researcher's choice wins

- **WHEN** the configuration sets an endpoint and the researcher then chooses AmSC MAG
- **THEN** requests go to MAG
