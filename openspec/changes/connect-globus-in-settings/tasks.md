## 1. Settle the two unknowns before building on them

Both are probes against real Globus with a personal token minted by
`./scripts/get_globus_token.py --print-token`. Neither uses the deployment refresh tokens. See
design.md — Risks; the first can reshape the change.

- [x] 1.1 ~~Determine whether one Globus account's token can drive transfers for both OLCF
      enclaves.~~ **Dropped, by the maintainer's decision.** `main` already pairs two per-cluster
      refresh tokens with a single `vista_globus_collection_id`, so this change inherits the
      arrangement rather than introducing it. If it is wrong it is wrong today, and fixing it is
      its own change
- [x] 1.2 Create a Globus Connect Personal endpoint through the Transfer API and confirm the
      response carries a `globus_connect_setup_key`; verify which scopes the call requires by
      requesting the narrowest set that works, starting from `transfer.api.globus.org:gcp_install`
      as observed in the address Globus Connect Personal's own setup prints. It works, and needs
      no extra scope: the base `transfer.api.globus.org:all` that `get_globus_token.py` already
      requests was enough, so `gcp_install` was a wrong guess in the harmless direction. The path
      needs its version prefix, `/v0.10/endpoint`, and `globus_sdk` 4.7 has no `create_endpoint`,
      so it is `TransferClient.post`. Throwaway endpoint created and deleted
- [x] 1.3 Feed that setup key to `gcp_vm.Endpoint.setup` on a scratch data directory and confirm
      `client-id.txt` is written with no terminal involved; verify the collection appears in the
      Globus web interface, and delete it afterwards. `setup(key, interactive=False)` printed
      "setup completed successfully" and wrote a `client-id.txt` holding the created collection's
      own id. After `start`, Globus reported `gcp_connected=True` for that collection, which is the
      same fact the web interface shows. Endpoint deleted
- [x] 1.4 Record what 1.1 and 1.2 returned in design.md, replacing the inferences they test

## 2. The credential, end to end, with no interface yet

- [x] 2.1 Add `odo_globus_token` and `frontier_globus_token` to the user schemas and
      `_USER_CONFIG_NULLABLE_FIELDS`, keeping `globus_token` as the shared fallback; verify
      `_add_missing_columns` adds them to an existing database at startup with no migration.
      Encrypted columns, in all four schema variants and the table, so they reach the MCP server
      in the metadata blob without any change to how that blob is built -- checked by dumping
      `UserPublicWithConfig` and seeing all three fields on the wire
- [x] 2.2 Declare all three on `UserConfig` and add `require_globus_token(cluster)` preferring the
      user's over the deployment's, mirroring `require_s3m_token`; verify by unit test that a
      user token wins, that the shared field is the second choice, that the deployment variable is
      the third, and that the error names where to connect when there is none.
      `test_globus_token_resolution.py`, nine tests. `Settings.require_globus_token` stays as the
      last source and its message stopped naming the environment variable, which a packaged
      researcher has no way to set
- [x] 2.3 Resolve the token through the user's configuration at the five `submit_job_mcp.py` call
      sites that currently read `settings` alone; verify the existing hermetic tests still pass and
      that no call site reads `settings.require_globus_token` directly. All five already had `cfg`
      in scope; `settings.require_globus_token` no longer appears in the file
- [x] 2.4 Invert `backend/tests/test_inference_credentials.py:276-286`, whose docstring asserts the
      Globus field must *not* be offered; verify it now asserts the opposite for the same reason.
      It asserted the field must not be offered *because nothing read it*; it now asserts the
      reading side, which is what stopped being true. The interface side is left to group 4, where
      the control exists -- what the interface will offer is an authorization, not a box to type a
      token into, so inverting the UI assertion now would have asserted something this change never
      builds

## 3. The authorization flow

- [ ] 3.1 Add a backend service that starts the flow: build the native-app client, generate a PKCE
      verifier, and return the authorization address with the cluster's SSO domain pinned, asking
      for the same scopes `get_globus_token.py` does and no more, since 1.2 showed those suffice to
      create the collection; verify by unit test that each cluster gets its own
      `session_required_single_domain` and that the verifier is never in the response
- [ ] 3.2 Hold the verifier server-side, keyed to the user, with an expiry; verify by unit test that
      a code exchanged after expiry is refused with a message telling the researcher to start again,
      and that one user's pending flow is not reachable by another
- [ ] 3.3 Add the completing call: exchange the code, keep the Transfer refresh token, store it on
      the user's per-cluster field; verify by unit test against a faked token response that the
      stored value is the refresh token and not the access token
- [ ] 3.4 Translate the failures Globus returns into statements a researcher can act on — a code
      pasted from a stale authorize URL is the common one, and `get_globus_token.py:322-340` already
      words it; verify by unit test that a PKCE mismatch names the recovery rather than surfacing
      the API error
- [ ] 3.5 Add `globus_sdk` to the backend's dependencies; verify `uv sync` resolves and the backend
      still starts

## 4. Connect Globus in the settings interface

- [ ] 4.1 Add a Connect Globus control per cluster to `UserSettingsModal.tsx`, showing the
      authorization address as something selectable and openable and a box for the code; verify it
      reports connected, not connected, and in-progress distinctly
- [ ] 4.2 Keep it out of the modal's save-diff path, since it is an exchange rather than a value to
      save; verify that saving other fields neither starts nor cancels a pending connection
- [ ] 4.3 Show which identity a connection was made with, so a researcher can tell the two enclaves
      apart; verify the identity comes from the token response rather than from what was typed
- [ ] 4.4 Report a failed exchange in place, keeping the address so the researcher can retry without
      starting over; verify the message names the cause from task 3.4

## 5. The endpoint's new owner

- [ ] 5.1 Add a lifespan to `vista_mcp_server` that stops the endpoint when the server stops; verify
      no microVM survives the server exiting, including on SIGTERM
- [ ] 5.2 Create the collection on first need, from a setup key obtained with the caller's token;
      verify an installation with a token and no collection reaches a running endpoint without a
      terminal, and that one that already has a collection does not create a second
- [ ] 5.3 Start the endpoint lazily under a lock; verify by test that concurrent tool calls produce
      one endpoint rather than several, and that a start already in progress is waited on rather
      than repeated
- [ ] 5.4 Report the wait as a step in progress rather than a silent pause, and report a failure
      with the cause from `gcp_vm.status`; verify the message distinguishes no credential, no
      collection, and an endpoint that would not start
- [ ] 5.5 Remove the refresh-token gate, the fourth managed service and the startup line from
      `scripts/package_launcher.sh`, and the equivalent block from `scripts/launch.sh`; verify both
      still start everything else and that `scripts/launch_globus.py` and the `python -m` entry
      point still work for a headless install

## 6. Tests

- [ ] 6.1 Add a hermetic test for the whole credential resolution — user per-cluster, user shared,
      deployment, none — driven through a tool call rather than the helper alone; verify it passes
      under `not live and not hpc and not sandbox`
- [ ] 6.2 Extend `scripts/smoke_test_package.sh` so a package with no credential starts, serves
      chat and retrieval, and reports file transfer as not connected; verify it uses `skip` for the
      endpoint itself, which cannot run without a credential
- [ ] 6.3 Add a `sandbox`-marked test that the lazy start produces exactly one endpoint under
      concurrent callers; verify it is excluded from the hermetic filter and passes when run
      deliberately

## 7. Documentation

- [ ] 7.1 Replace the exported-variable instructions in `README.md` with the interface flow, keeping
      the variables documented as the deployment fallback; verify no instruction tells a desktop
      researcher to export anything
- [ ] 7.2 Update the Globus paragraph in `AGENTS.md`; verify it describes where the credential comes
      from and who owns the endpoint process

## 8. Verification

- [ ] 8.1 Connect Globus for one cluster in the interface on a packaged installation, from nothing:
      no environment variable, no collection, no terminal; verify the collection appears online in
      the Globus web interface
- [ ] 8.2 Confirm the other cluster still reports as not connected, and connect it too; verify both
      report the identity they were connected with
- [ ] 8.3 Run `./scripts/ci-local.sh` and verify it is green
- [ ] 8.4 Confirm the hosted path is untouched: with the deployment variables set and no user
      connection, verify a file operation uses the deployment credential exactly as before

## 9. Gated, not automated

- [ ] 9.1 One end-to-end submit to Odo through a credential connected in the interface. **Requires
      explicit approval and is run by the maintainer, not by any automation.** Do not perform this
      step, or any other that contacts an OLCF machine, without being asked
- [ ] 9.2 Frontier only after asking OLCF, and separately from Odo
