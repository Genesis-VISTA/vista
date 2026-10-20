## ADDED Requirements

### Requirement: Expired Globus session on file operations
An Odo or Frontier file operation that meets a Globus refusal reconnecting
fixes SHALL fail saying the researcher's Globus session for that cluster has
expired and to reconnect Globus for it in the VISTA user settings, and SHALL
include Globus's own explanation when it gives one. This SHALL hold however
Globus phrases the refusal: a 401, a refusal asking the researcher to
authenticate again whatever its HTTP status, or a refresh token Globus will no
longer exchange. Such a refusal SHALL NOT be reported as a missing file or as
a generic refusal to serve the file.

#### Scenario: Expired session over HTTPS
- **WHEN** a job-log poll asks for a file's size on Odo and the collection answers 403, code `InsufficientSession`, asking for a login with opensso.ccs.ornl.gov
- **THEN** the operation fails saying the Globus session for Odo has expired and to reconnect it in the VISTA user settings, includes Globus's message naming opensso.ccs.ornl.gov, and does not say Globus refused to serve the file

#### Scenario: Expired session on a listing
- **WHEN** a Frontier directory listing fails with HTTP 502, code `ExternalError.DirListingFailed.LoginFailed`, asking for a new session
- **THEN** the operation fails saying the Globus session for Frontier has expired and to reconnect it

#### Scenario: Missing file is still a missing file
- **WHEN** the collection answers 404 for the requested path
- **THEN** the operation reports that the path is not on the cluster, not an expired session
