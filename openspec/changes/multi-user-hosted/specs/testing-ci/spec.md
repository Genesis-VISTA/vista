## ADDED Requirements

### Requirement: Tenant isolation suite

Hermetic PR CI SHALL run a tenant isolation suite that, for every backend route serving owned
data, signs in two users with test-minted tokens and checks that one user's request for the
other's data answers `404` and that listings contain only the caller's own. A route added
without a case SHALL fail the suite.

#### Scenario: A new route without a case

- **WHEN** a route that takes an owned id is added and the suite has no case for it
- **THEN** the suite fails and names the route
