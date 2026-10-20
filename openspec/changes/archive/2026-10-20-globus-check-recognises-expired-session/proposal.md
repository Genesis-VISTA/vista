## Why

Issue #78 (Genesis Hackathon feedback, "Globus token doc (debugging tips)"): with an expired
Globus session, the Odo and Frontier cards say "Couldn't verify" and don't point to the cause.
"Globus session expired" has never been seen in the UI.

Probing a live expired session (2026-10-20, the test admin's Odo and Frontier pairs) showed
why. When the OLCF High Assurance login lapses after its 3 days, both refresh tokens still
exchange, and the Transfer listing the card makes fails with `HTTP 502`,
code `ExternalError.DirListingFailed.LoginFailed`, and a body that is a Globus
re-authentication answer (a GARE: `authorization_parameters` naming the login it needs,
`session_required_single_domain: ["opensso.ccs.ornl.gov", "clients.auth.globus.org"]` for
Odo, `sso.ccs.ornl.gov` for Frontier). The check recognises an expired session only as a 401,
`ConsentRequired` or `AuthenticationFailed`, so this falls through to Couldn't verify, and the
log keeps only the exception's type.

The MCP server's file operations misread the same session. Over the HTTPS interface every
path answers `403`, code `InsufficientSession`, with a GARE body, not the 401 that
`lib/globus.py` and AGENTS.md say a lapsed session returns. A chat fetching job output is told
"Globus refused to serve <path> (HTTP 403)" instead of to reconnect.

## What Changes

- The card's Globus check treats any Globus refusal carrying a GARE as an expired session,
  whatever its HTTP status, alongside the existing 401 and code rules. The detail says to
  connect Globus again and names the login Globus requires ("Globus needs a fresh login with
  opensso.ccs.ornl.gov").
- A refresh token Globus refuses outright (a 4xx from the token exchange, such as
  `invalid_grant`) stays an expired session, with its own detail: Globus no longer accepts the
  saved connection.
- Every other failure stays Couldn't verify, but its detail says what happened: the HTTP
  status and Globus's code and message, or that Globus did not answer in time. A 5xx from
  Globus Auth during the token exchange stops being reported as an expired session.
- A failed check logs its HTTP status, Globus code and a truncated body.
- The card's expired Globus row shows the check's message rather than fixed text.
- The MCP server's file operations report an expired session the same way: a Transfer error or
  an HTTPS answer whose body is a GARE is an expired session, and `stat` reads the body of a
  403 as well as a 401.
- AGENTS.md and the `lib/globus.py` docstrings stop saying a lapsed session answers 401.
- `docs/hpc.md` gains a short section on the Globus card's states: cause, remedy, and where the
  log line is.

No new cluster state, no change to the state precedence, and no extra call to Globus. When the
S3M token is also rejected, the card still reads Token rejected, and its details list the
expired Globus session.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `hpc-availability`: the Globus check's Session expired scenario widens from "the collection
  answers 401" to any re-authentication refusal and a refused token exchange, naming the
  required login; a new scenario says what any other Globus failure reports.
- `hpc-job-submission`: a file operation that meets an expired Globus session reports it as
  expired, however Globus phrases the refusal.

## Impact

- `backend/src/vista_backend/services/hpc_status.py`: `probe_globus`, `_globus`.
- `ui/components/HpcStatusSection.tsx`: `globusRow`.
- `mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/globus.py`:
  `_raise_for_transfer_error`, `_check`, `stat`, docstrings.
- Tests: `backend/tests/test_hpc_status.py`, `ui/tests/HpcStatusSection.test.tsx`,
  `mcp_servers/vista_mcp_server/tests/test_globus_https.py`.
- Docs: `AGENTS.md`, `docs/hpc.md`.
- No new dependency: `globus_sdk.gare` ships with the locked `globus-sdk` (4.9 in the
  backend, 4.7 in the MCP server). Both projects' `globus-sdk>=3.40` floor rises to `>=4.7`.
