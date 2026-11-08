## MODIFIED Requirements

### Requirement: Globus check for OLCF clusters
For Odo and Frontier the system SHALL verify the Globus connection the
cluster's file operations would use. It SHALL choose the same credential
source, in the same order, that those operations choose. Only credentials the
researcher connected count; there is no deployment-wide credential. It SHALL
verify the connection with a live call to the cluster's collection, so that an
expired High Assurance session is detected rather than reported as connected.
It SHALL NOT estimate when a session will lapse.

The check SHALL fail as Session expired whenever Globus refuses in a way that
reconnecting Globus fixes: a refusal that asks the researcher to authenticate
again, whatever its HTTP status, or a refresh token Globus will no longer
exchange. Its detail SHALL say to connect Globus again for that cluster and,
when Globus names the login it requires, name that login. Any other failure
SHALL be Couldn't verify, with a detail that says what Globus answered or that
it did not answer in time; it SHALL NOT be reported as Session expired. Every
failure SHALL be logged with Globus's HTTP status, error code and the start of
its response, and no detail or log line SHALL carry a token.

#### Scenario: Globus verified
- **WHEN** the researcher has a complete Globus credential for the cluster and a read-only call to its collection succeeds
- **THEN** the Globus check passes

#### Scenario: Globus missing or incomplete
- **WHEN** none of the researcher's sources holds both the Transfer and the collection token for the cluster
- **THEN** the Globus check fails as Not connected

#### Scenario: Deployment credential does not count
- **WHEN** the researcher has connected no Globus account and `VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN` and `VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN` are set in the environment
- **THEN** Odo's Globus check fails as Not connected

#### Scenario: Globus session expired
- **WHEN** the collection answers 401
- **THEN** the Globus check fails as Session expired, distinct from Not connected

#### Scenario: High Assurance login lapsed
- **WHEN** the token exchange succeeds and Globus refuses the call to Odo's collection with HTTP 502, code `ExternalError.DirListingFailed.LoginFailed`, asking for a new session in `opensso.ccs.ornl.gov`
- **THEN** the Globus check fails as Session expired, and its detail says to connect Globus again for Odo and that Globus needs a login with opensso.ccs.ornl.gov

#### Scenario: Saved connection no longer accepted
- **WHEN** Globus refuses to exchange the researcher's refresh token for Frontier with HTTP 400 `invalid_grant`
- **THEN** the Globus check fails as Session expired, and its detail says Globus no longer accepts the saved Frontier connection and to connect Globus again

#### Scenario: Globus fails otherwise
- **WHEN** Globus answers the token exchange or the call to the collection with HTTP 503, or does not answer within the check's time limit
- **THEN** the Globus check fails as Couldn't verify, not Session expired, and its detail names the HTTP status or says Globus did not answer in time
