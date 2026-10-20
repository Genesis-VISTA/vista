## Context

See proposal.md for why. Two copies of "is this an expired session?" exist, and both stay:
`probe_globus` in the backend's `services/hpc_status.py` (the card) and `lib/globus.py` in the
MCP server (file operations). The backend does not depend on the MCP package.

What Globus actually returns, from probing a live expired session on 2026-10-20 (the test
admin's Odo and Frontier pairs, decrypted from `vista.db`):

| Call | Expired High Assurance login |
|---|---|
| Refresh-token exchange (both halves) | succeeds |
| Transfer `operation_ls /~/` | `502`, code `ExternalError.DirListingFailed.LoginFailed`, GARE body: `session_message` "Session reauthentication required (Globus Transfer)", `session_required_single_domain` `["opensso.ccs.ornl.gov", "clients.auth.globus.org"]` (Odo; `sso.ccs.ornl.gov` for Frontier) |
| Transfer `get_endpoint` | succeeds |
| HTTPS `HEAD` / `GET`, any path | `403`, code `InsufficientSession`, GARE body: `session_message` "You must authenticate or link with an identity from one of these domains (opensso.ccs.ornl.gov, clients.auth.globus.org) which has been granted access to this collection." `HEAD` carries no body. |

A refresh token Globus will not exchange answers `400 {"error":"invalid_grant"}`, with no code
and no message (seen when ciphertext was sent by mistake; the same answer as a revoked token).

## Goals / Non-Goals

**Goals:**
- Recognise an expired session by what Globus says it needs, not by a list of codes.
- The card and file operations agree on which refusals mean "reconnect".

**Non-Goals:**
- One shared module for both copies.
- An HTTPS call from the card. Transfer already catches the lapse, with a better signal.
- New cluster states or precedence changes.

## Decisions

**1. A GARE body means expired, checked before the status code.** `globus_sdk.gare.is_gare`
accepts either an SDK error or a parsed JSON dict, so both surfaces use it: the Transfer error in
`probe_globus` and `_raise_for_transfer_error`, and the HTTPS body in `_check`. It runs before any
status rule, because the live lapse is a `502`, which a status-first rule would read as an outage.
The existing 401 / `ConsentRequired` / `AuthenticationFailed` rules stay after it.
*Alternative:* add `ExternalError.DirListingFailed.LoginFailed` and `InsufficientSession` to the
code list. Rejected: the codes are per-surface and per-endpoint-type, and the GARE is Globus's
documented, general signal that re-authentication fixes the refusal.

**2. Only `invalid_grant` from the exchange is expired.** The exchange raises `AuthAPIError`. A
`400` with `error: invalid_grant` means Globus will no longer accept that refresh token, which
reconnecting fixes. Any other Auth error (a 5xx, `invalid_client` from a misconfigured client
ID) is Couldn't verify with its status and code. Today every `GlobusAPIError` there reads as
expired.

**3. The card names the login; the file operations quote Globus.** The Transfer GARE's
`session_message` says nothing useful, so the card builds its detail from
`session_required_single_domain`, leaving out `clients.auth.globus.org` (Globus's own
app-client domain, never the researcher's login): "The Globus session for Odo has expired:
Globus needs a fresh login with opensso.ccs.ornl.gov. Connect Globus again." With no domain,
it gives the plain remedy, never "Globus said: None". The file operations keep the existing
`_session_expired(detail)` wording and `_gare_message`, because the HTTPS `session_message`
already names the domains.

**4. The probe raises a typed outcome carrying its detail.** `probe_globus` raises
`GlobusSessionExpired(detail)` or a new `GlobusCheckFailed(detail, log)`. `_globus` puts the
detail in `Check.message` and `http_status`, and logs the `log` part. `asyncio.wait_for`'s
`TimeoutError` and `globus_sdk.NetworkError` become "Globus did not answer within 10 s" and
"Globus could not be reached". The UI's `globusRow` shows `check.message` for `session_expired`.

**5. Log the status, code and up to 500 characters of the body, at `info`.** The same level as the
other card checks. Globus error bodies carry no tokens (both live bodies were inspected). The
`Authorization` header is never logged.

**6. `stat` reads the body of a 403 too.** A `HEAD` has no body, and the GARE lives in the body,
so the existing one-byte follow-up `GET` runs on `403` as well as `401`. A 403 without a GARE
(for example a real permission denial) is still a refusal.

## Risks / Trade-offs

- [A GARE for a reason other than a lapsed session, such as a missing consent with
  `required_scopes`] → Reconnecting Globus in Settings fixes those too, so "expired, reconnect"
  stays the right remedy. The detail still names the login when Globus gives one.
- [Globus changes the shape of its refusals] → The GARE format is a published Globus spec, and
  the log line now records whatever comes back, so an unrecognised shape is diagnosable from
  `logs/backend.log`.
- [The Token rejected state hides an expired Globus session on the card face] → Accepted
  (decided in grilling): the card's details list every failed check.
- [The two copies drift again] → Each side's tests use the same live bodies as fixtures.
