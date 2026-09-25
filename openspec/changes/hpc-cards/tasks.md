## 1. Per-cluster S3M tokens

- [x] 1.1 Add encrypted `odo_s3m_token` / `frontier_s3m_token` columns to `app_user` (nullable, blank→null) and expose them on `GET/PUT /users/me`. Remove `s3m_token` from every API schema, but leave the column. Verify with a round-trip test in `backend/tests/test_access_control.py` or a new `backend/tests/test_user_tokens.py`, and check that an existing DB gains the columns on startup
- [x] 1.2 Send the two tokens in the MCP `_meta.vista.user` under those names and stop sending `s3m_token`. Verify with a test asserting the metadata contents, alongside the existing tests in `backend/tests/test_mcp_invoke.py`
- [x] 1.3 In the MCP server, remove `UserConfig.s3m_token` and the fallback in `require_s3m_token`, and make `submit_job_mcp.py`'s cluster enablement depend only on the per-cluster tokens. Verify with `mcp_servers/vista_mcp_server` tests: a legacy-only config enables neither cluster, and each token enables only its cluster
- [x] 1.4 Replace the single S3M field in `UserSettingsModal.tsx` and `ui/lib/user.ts` with Odo and Frontier fields, still in the current layout. The rework in group 5 regroups them. Verify with `npm run lint` and a hermetic check that saving one leaves the other untouched

## 2. Backend: settings and visibility column

- [x] 2.1 Add backend settings for cluster endpoints, read from the same `VISTA_MCP_*` env var names as the MCP server: IRI URLs, S3M introspect URL, Odo/Frontier accounts, resource match names, Globus collection ids, and deployment Globus pairs. Verify with a new case in `backend/tests/test_settings_defaults.py`
- [x] 2.2 Add a hermetic parity test, `backend/tests/test_hpc_config_parity.py`, that parses `mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py` and asserts the backend defaults match. Verify it fails when one default is edited
- [x] 2.3 Add `hpc_hidden_clusters` (nullable JSON list) to `app_user` and expose it on `GET/PUT /users/me`, rejecting unknown cluster names. Verify with a round-trip test and a rejection test

## 3. Backend: checks and state resolution

- [x] 3.1 Implement `resolve_state` as a pure function with the spec's precedence, including Couldn't verify. Verify with a table-driven test covering every `Cluster state` scenario in `backend/tests/test_hpc_status.py`
- [x] 3.2 Implement the facility check: public status list plus incidents, matching resources by name/group, ignoring ended incidents, a 5 s timeout, and a 60 s shared cache. Verify with `httpx.MockTransport` fixtures built from the spike's response shapes, including OLCF's resolved incident and a timeout
- [x] 3.3 Implement the credential check: `compute/resources` with the token (200 ok, 401 rejected, anything else unverifiable), plus for Odo/Frontier S3M introspect (project match, `plannedExpiration`, future `delayDate` → "not active until"). Verify every case, and that having no token means no outbound call
- [x] 3.4 Implement the Globus check: the credential-source order mirrored from `UserConfig.require_globus_token` (the deployment pair counts), refreshing both tokens, Transfer `ls` of the home dir with limit 1, and 401/`ConsentRequired` → Session expired. Verify with faked Globus clients for own / shared / deployment / incomplete / expired
- [x] 3.5 Add `GET /users/me/hpc-status?fresh=&cluster=`, running the visible clusters concurrently with a 60 s per-cluster cache keyed on the credential hash. Verify that:
  - hidden clusters make no calls;
  - `fresh=true` rechecks everything, and `fresh=true&cluster=odo` rechecks only Odo;
  - saving a token invalidates the cache;
  - no token substring appears in any response, including facility error bodies.

## 4. UI: data, proxy, and rail

- [ ] 4.0 Update the HPC Availability Cards canvas to the spec, following design.md Decision 7: drop Lux, replace "Ready · files not connected" with Globus not connected, rename Token expired to Token rejected, add Couldn't verify / Wrong project / Globus session expired / stale-note states, and add a settings-modal artboard with one cluster expanded. Verify that the user has approved the revised canvas before building 4.3
- [ ] 4.1 Read the route-handler docs in `ui/node_modules/next/dist/docs/`, then add `ui/app/api/users/me/hpc-status/route.ts` following `users/me/route.ts`. Verify with a hermetic stub in `ui/e2e-hermetic/stub.ts`
- [ ] 4.2 Add a shared `useHpcStatus()` store in `ui/lib/hpc-status.ts`: fetch on mount, a 5 min poll while visible, `recheck(cluster?)`, and keeping the last result plus `checkedAt` on failure, with Couldn't verify past 15 min. Verify with `ui/tests/hpc-status.test.ts` using fake timers, a visibility toggle, and a failing fetch
- [ ] 4.3 Build `ui/components/HpcStatusSection.tsx`: expanded cards, the details popover (per-check rows, S3M expiry for Odo/Frontier only, checked-at, Recheck, Settings link), collapsed items with `"<Cluster>: <State>"` accessible names, and a dot shape per state, matching the approved canvas. Verify with `ui/tests/HpcStatusSection.test.tsx` rendering every state, and that no Perlmutter or Globus expiry text appears
- [ ] 4.4 Mount it in `NavRail.tsx` under Opened Project, and hide the section when all clusters are hidden. Verify with an assertion in `ui/e2e-hermetic/shell.spec.ts`

## 5. UI: settings modal rework

- [ ] 5.1 Restructure `UserSettingsModal.tsx` into identity and model settings at the top, then one collapsible section per cluster. Each section has its status in the header, a "Show in sidebar" switch, and that cluster's credentials: the S3M token and Globus Connect for Odo/Frontier; account, remote dir, and IRI token for Perlmutter. Verify with `ui/tests/UserSettingsModal.test.tsx`: all sections collapsed by default, and each field in its cluster's section
- [ ] 5.2 Add the `initialCluster` prop and wire the popover's Settings link through `NavRail`. Verify in `ui/e2e-hermetic/shell.spec.ts` that the Frontier card → Settings opens with only Frontier expanded
- [ ] 5.3 Recheck the cluster after its credentials are saved or Globus Connect completes. Verify in `ui/tests/UserSettingsModal.test.tsx` that `recheck("odo")` is called once after saving an Odo token
- [ ] 5.4 Verify in `ui/e2e-hermetic/shell.spec.ts` that hiding Perlmutter removes its card across a reload and leaves its token untouched

## 6. Integration and visual check

- [ ] 6.1 Run `./scripts/ci-local.sh lint` and `./scripts/ci-local.sh test`, and verify all targets (backend, ui, mcp) are green
- [ ] 6.2 With `./launch.sh logs`, take Playwright screenshots of the expanded rail, the collapsed rail, an open popover, and the settings modal deep-linked to one cluster. Compare them against the HPC Availability Cards canvas, and verify the tokens and focus rings

## 7. Live verification (manual; `live` marker, excluded from PR CI)

- [ ] 7.1 Add `backend/tests/live/test_hpc_status_live.py` (`@pytest.mark.live`, skips without credentials), asserting each cluster with credentials resolves to Ready or a precise failure. Verify it is deselected by `-m "not live and not hpc and not sandbox"`
- [ ] 7.2 Run 7.1 against Odo with a real token and Globus connection, and confirm Ready. Then run it after revoking or expiring the Globus session. If Transfer `ls` doesn't fail the way the HTTPS surface does, add an HTTPS `HEAD` on the collection home to the Globus check
- [ ] 7.3 Once Frontier access works, run 7.1 with a real Frontier S3M token, and confirm `compute/resources` returns 200 for a valid token and 401 for an invalid one, as on Odo. Record the result here. Perlmutter stays unverified
