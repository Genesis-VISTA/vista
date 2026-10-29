## REMOVED Requirements

### Requirement: The default is named

**Reason**: Its only scenario is a fresh install on AmSC i2, a provider that no longer exists.
**Migration**: Replaced by "The provider's default is named", the same behaviour on AmSC MAG.

## ADDED Requirements

### Requirement: The provider's default is named

When the researcher has not chosen a model and their provider has a default, the picker's
label SHALL name it, as "Default (<model>)".

#### Scenario: Fresh install on MAG

- **WHEN** a researcher on AmSC MAG has never chosen a model
- **THEN** the picker reads "Default (claude-sonnet)"
