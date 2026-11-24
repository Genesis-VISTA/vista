## ADDED Requirements

### Requirement: A server-provided model when hosted

A hosted deployment SHALL offer a Server provider whose endpoint and key are server settings
(Holly's Beam by default), which researchers use without entering any key and which SHALL be
the default for a new researcher. Researchers MAY also choose MAG or OLCF Inference with their
own key, stored in their user settings. The Custom provider SHALL be refused when hosted. A
desktop install SHALL NOT offer the Server provider.

#### Scenario: A new researcher's first chat

- **GIVEN** a hosted deployment with Beam configured
- **WHEN** a researcher who has never opened Settings sends a message
- **THEN** it is answered by Beam

#### Scenario: Custom on hosted

- **WHEN** a researcher on a hosted deployment saves the Custom provider
- **THEN** the save is refused with the reason

### Requirement: The server model can be unavailable

When the server model's endpoint does not answer, the Server provider SHALL be reported as
unavailable with the reason, conversations on it SHALL fail with that reason, and VISTA SHALL
keep serving everything else, including conversations on a researcher's own MAG or OLCF key.

#### Scenario: Beam is stopped

- **GIVEN** Beam is stopped on a hosted deployment
- **WHEN** a researcher using MAG with their own key sends a message
- **THEN** it is answered, and the model picker shows the Server model as unavailable
