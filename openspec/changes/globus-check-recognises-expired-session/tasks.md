## 1. Card: classify Globus failures

- [ ] 1.1 Add Globus error fixtures to `backend/tests/test_hpc_status.py`, built from the live bodies in design.md's Context: the Transfer `502 ExternalError.DirListingFailed.LoginFailed` GARE (Odo's domains), the exchange `400 {"error":"invalid_grant"}`, an exchange `503`, a Transfer `503` with no GARE, and a Transfer `400` with a plain code. Extend `_globus_error` to take a full body. Verify that the new tests in 1.2 fail against today's `probe_globus`
- [ ] 1.2 In `probe_globus`, check for a GARE first (`globus_sdk.gare.is_gare`), whatever the status, and raise `GlobusSessionExpired` with a detail naming the required login from `session_required_single_domain`, minus `clients.auth.globus.org`. Keep the 401 / code rules and the 403/404-proves-the-session rule after it. Verify that the 502 GARE fixture raises `GlobusSessionExpired` whose detail names `opensso.ccs.ornl.gov`, and that the existing `test_probe_*` tests still pass
- [ ] 1.3 Make only `invalid_grant` from the token exchange an expired session, with the "no longer accepts the saved connection" detail. Any other exchange error, and any remaining Transfer error, raises a new `GlobusCheckFailed` whose detail names the HTTP status, Globus's code and its message. Verify that the exchange `503` and the Transfer `503` / `400` fixtures raise `GlobusCheckFailed`, never `GlobusSessionExpired`
- [ ] 1.4 In `_globus`, put the exception's detail in `Check.message` (and the status in `Check.http_status`). Map `TimeoutError` to "Globus did not answer within 10 s" and `globus_sdk.NetworkError` to "Globus could not be reached". Log the status, code and up to 500 characters of the body at `info`. Verify with service-level tests: expired → `globus_session_expired` with the login in the message; 503 / timeout → `unverifiable` with the status or time limit in the message; and `caplog` shows the code and body but no token
- [ ] 1.5 Raise `globus-sdk>=3.40` to `>=4.7` in `backend/pyproject.toml` and run `uv lock`. Verify that the lock still resolves 4.9.0 and `./scripts/ci-local.sh backend` passes

## 2. Card: show the detail

- [ ] 2.1 In `globusRow` (`ui/components/HpcStatusSection.tsx`), show `check.message` as the `session_expired` row's detail instead of the fixed text. Verify with a case in `ui/tests/HpcStatusSection.test.tsx`: an Odo status whose Globus check is `session_expired`, with a message naming `opensso.ccs.ornl.gov`, renders that message, and `./scripts/ci-local.sh ui` passes

## 3. File operations: same rule in the MCP server

- [ ] 3.1 In `lib/globus.py`, make `_raise_for_transfer_error` treat a GARE as an expired session before its existing rules. Verify with a test in `mcp_servers/vista_mcp_server/tests/test_globus_https.py` (or its Transfer tests) using the live 502 body: the listing raises `GlobusSessionExpired` and `operation_mkdir`'s "already exists" handling is unchanged
- [ ] 3.2 Make `_check` treat a response whose body is a GARE as an expired session, whatever its status, and have `stat` fetch the one-byte body on a `403` as well as a `401`. Verify with the live `403 InsufficientSession` body: `stat` raises `GlobusSessionExpired` whose text includes Globus's message naming `opensso.ccs.ornl.gov`; a `403` with no GARE still raises the generic refusal; a `404` still raises `GlobusFileNotFound`
- [ ] 3.3 Raise `globus-sdk>=3.40` to `>=4.7` in `mcp_servers/vista_mcp_server/pyproject.toml` and run `uv lock`. Verify that `./scripts/ci-local.sh mcp` passes

## 4. Docs

- [ ] 4.1 Correct every claim that a lapsed session answers 401: the Globus section of `AGENTS.md`, and the `GlobusSessionExpired`, `GlobusFileNotFound`, `_check` and `stat` docstrings in `lib/globus.py`. Say instead that Globus asks for a new session (a GARE) under various statuses: `502` from Transfer, `403 InsufficientSession` over HTTPS. Verify by grepping for "401" in both files and reviewing each hit
- [ ] 4.2 Add a "When the Globus card isn't Ready" subsection under `### Globus` in `docs/hpc.md`. For Globus not connected, Globus session expired (lapsed login vs a saved connection no longer accepted) and Couldn't verify, give the cause, what to do, and where the "Globus check failed" line is in `logs/backend.log`. Verify by reading it against the card's actual messages from group 1

## 5. Live verification

- [ ] 5.1 Before the test admin reconnects Globus, run a stack on this branch against a copy of their data, then check Odo and Frontier. Verify that each card's Globus row reads "Globus session expired", names `opensso.ccs.ornl.gov` / `sso.ccs.ornl.gov`, and that the log line shows the 502 code. Then reconnect Globus for Odo and verify the row passes
