<!--
Groups run through apply-in-groups-delegate; "Needs" lists the groups whose merge into
vfs-multi-user a group waits for. See design.md "Delivery plan" and the dashboard.
-->

## 1. Fix-first hardening (wave 1; needs nothing)

- [x] 1.1 `GET`/`PUT /settings` return `has_*` flags for every credential instead of values (`api/settings.py`, `db/schemas.py` `LocalSettingsPublic`, `services/local_settings.to_public`); verify with a new `backend/tests/test_settings_no_secrets.py` that saves each credential and asserts no response body contains it
- [ ] 1.2 The settings modal and `ui/lib/settings.ts` use the flags: a set credential shows as saved with Replace/Clear, never its value; verify with `cd ui && npm run lint` and the UI lane's settings tests, plus a hands-on check in the running app
- [x] 1.3 A hosted backend refuses to start without `VISTA_BACKEND_ENCRYPTION_KEY`, with the development key, or with a key file under the data folder (`config.py:491-499`); verify with `backend/tests/test_hosted_startup.py`
- [ ] 1.4 Backend and VISTA MCP server bind `127.0.0.1`; the UI's `next start` binds loopback (`scripts/launch.sh` ~132); verify with a launch-script test or `ss -ltnp`/`lsof -iTCP -sTCP:LISTEN` on a dev stack (hands-on)
- [x] 1.5 MCP shared secret: `launch.sh` generates `VISTA_MCP_SECRET`; the backend sends `X-Vista-MCP-Secret`; the MCP HTTP app refuses requests without it; verify with `mcp_servers/vista_mcp_server/tests/test_shared_secret.py` and the backend's MCP health test
- [x] 1.6 `POST /projects/{name}/mcp/call` answers `404` when hosted (`api/mcp.py:41-58`); verify with a test in `backend/tests/test_mcp_call.py`

## 2. Hosted flag and base path (wave 1; needs nothing)

- [x] 2.1 The version route reports `deployment`; verify with a backend test for both values
- [x] 2.2 `ui/lib/deployment.ts` `useDeployment()` and a sign-out/user menu shown only when hosted; verify with a UI unit test for both values
- [x] 2.3 Backend refusals when hosted: update check/install (`api/updates.py:93-160`), NERSC IRI fields, Custom provider save, linked folders; verify with `backend/tests/test_hosted_refusals.py`
- [x] 2.4 UI hides the same controls plus Electron-only actions (`UpdateControls.tsx`, `ui/lib/version.ts` callers, NERSC section, Custom provider option); verify with UI tests rendering under `hosted`
- [x] 2.5 Skill import when hosted: `https` on `VISTA_BACKEND_SKILL_IMPORT_ALLOWED_HOSTS` only, resolved addresses in private/loopback/link-local ranges refused (`agents/skill_repo.py:134`); verify with tests using a fake resolver
- [x] 2.6 `VISTA_BASE_PATH` read into `basePath` in `ui/next.config.*`; one `apiUrl()` helper for every client `fetch`/`EventSource`; an ESLint rule against `'/api/` literals in client code; read `ui/node_modules/next/dist/docs/01-app/03-api-reference/05-config/01-next-config-js/basePath.md` first; verify with `VISTA_BASE_PATH=/vista/ui npm run build` and a Playwright smoke that opens a project and streams a chat under the path

## 3. Dev gateway and identity through the UI (wave 1; needs nothing)

- [ ] 3.1 A catch-all proxy `ui/app/api/[...path]/route.ts` forwarding method, query, body, streamed responses and `Authorization: Bearer` (from `X-Access-Token` or `Authorization`) via `_backend.ts`; relay-only route files removed; verify existing relay/SSE tests pass and a new test shows the header reaches a fake backend
- [ ] 3.2 `scripts/dev-gateway.py` (`uv run --script`): RSA key, JWKS at `/realms/dev/protocol/openid-connect/certs`, a name-picker login page and cookie, a reverse proxy to the UI injecting `X-Access-Token` (`azp=apisix`, role `apisix/vista`, `typ=Bearer`); verify with a script test that a minted token passes a copy of the verifier
- [ ] 3.3 `./launch.sh --hosted`: starts the gateway on `:3100`, sets deployment, issuer, a generated encryption key and `VISTA_ENV=dev`; verify by launching and signing in (hands-on; until group 4 lands the backend ignores the token)

## 4. CurrentUser: Sam's carve-out (wave 1; needs nothing; gate before waves 2–5)

- [ ] 4.1 Scaffold only (parent): `UserTable` (`app_user`) in `db/schemas.py`, `local` created in `init_db`, a stub `services/current_user.py` with `CurrentUser`'s signature, a token-minting fixture in `backend/tests/conftest.py`, failing `backend/tests/test_current_user.py` (desktop → `local`; hosted no/bad token → 401; ID token → 401; missing role → 403; new sub created once; JWKS down → 503), and a toolbox of PyJWT/`PyJWKClient` calls with examples from `collaboration_server/.../services/identity.py`
- [ ] 4.2 Sam implements `CurrentUser` and the verifier, and attaches it router-wide in `api/api.py` (health/version outside it); verify with `cd backend && uv run --extra dev pytest tests/test_current_user.py`
- [ ] 4.3 Parent reviews Sam's diff like a teammate's; gate passes when the review's findings are settled

## 5. Settings split (wave 2; needs 1, 4)

- [ ] 5.1 `user_settings` table; per-user fields per the `user-accounts` spec; one-time copy from `local_settings` for `local` on desktop; verify with `backend/tests/test_settings_split.py` including a pre-change `vista.db` fixture
- [ ] 5.2 `SettingsView` (server + current user's) replaces `LocalSettingsDep` at every call site; it raises outside a user context; verify with the full backend suite
- [ ] 5.3 Hosted server settings from the environment, read-only through the API with the reason; verify with tests for `PUT /settings` when hosted

## 6. Ownership and data-layer filtering (wave 2; needs 4)

- [ ] 6.1 Nullable `user_id` on project, chat_session, chat_run, campaign_run, hpc_job, debate_run, background_task, skill, knowledge_base; startup backfill to `local`; verify `db/schema_check.py` accepts a pre-change DB and the backfill test passes
- [ ] 6.2 `db/ownership.py` (`owned`, `get_owned`); every owned query uses it, including the name lookup in `services/project.py:31`; per-owner unique constraints for new DBs; verify with `backend/tests/test_ownership.py`
- [ ] 6.3 Bundled/seeded skills and the docs corpus readable by all, writable by none; verify with tests editing a bundled skill as two users

## 7. Authorization and background owners (wave 3; needs 6)

- [ ] 7.1 Chat-run streams, elicitations, project files and downloads check the owner and answer `404` (`api/chat_runs.py:31-58`, `services/project_agent.py:95`, `api/files.py`); verify with tests as two users
- [ ] 7.2 `as_user(user_id)` and its use in job watches, campaign monitor, inbox wakes and startup recovery (`services/background_tasks.py:1050`, `campaign_monitor.py:213`, `api/api.py:84-135`); verify with tests driving each entry point for two users

## 8. Runtime per user and sandbox caps (wave 3; needs 5, 6)

- [ ] 8.1 Agent pool keyed by user; settings change evicts only that user's agents; verify with `backend/tests/test_project_agent_pool.py`
- [ ] 8.2 HPC metadata carries only the current user's fields plus allowed clusters (`agents/agents.py:1002`); Globus pending keyed `(user, cluster)`; verify with tests
- [ ] 8.3 Sandbox volumes per user (desktop `local` keeps today's path); allowlisted sandbox environment instead of `**os.environ` (`agents/agents.py:402-430`); verify with a unit test of the env builder, and `-m sandbox` with `VISTA_RUN_SANDBOX=1` (not in PR CI)
- [ ] 8.4 Caps 4 per user / 40 total, LRU idle eviction within the user, refusal when none idle; verify with pool tests using `harness.FakeSandbox`

## 9. Odo-only on hosted (wave 3; needs 2, 5)

- [ ] 9.1 `allowed_clusters` server setting; `hpc_status`, settings saves, Globus logins filtered; verify with backend tests
- [ ] 9.2 MCP job and transfer tools refuse clusters outside the metadata's allowed list (`submit_job_mcp.py`, `transfer_mcp.py:47`); verify with `mcp_servers/vista_mcp_server/tests/` dry-run tests
- [ ] 9.3 S3M save introspects and requires open enclave; shared Globus pair refused and its fallback off when hosted (`lib/user_config.py` `require_globus_token`); verify with a faked introspection endpoint
- [ ] 9.4 Hosted agent instruction rule and UI showing Odo only; verify with a prompt snapshot test and a UI test

## 10. Inference server provider (wave 3; needs 2, 5)

- [ ] 10.1 `server` preset from server settings, default when hosted; MAG/OLCF keys per user; Custom refused; verify with `backend/tests/test_inference_providers.py` additions
- [ ] 10.2 Availability probe and picker state when Beam is down; verify with a fake endpoint test and a UI test

## 11. Collaboration auto sign-in (wave 4; needs 3, 5)

- [ ] 11.1 When hosted, the collab client uses the request's access token; status signed in; token field hidden; outside a request reports "needs the web app"; verify with the `collab_server` fixture and test-minted tokens (`--extra dev`)

## 12. Slack per user (wave 4; needs 6, 7)

- [ ] 12.1 `slack_link.user_id`, set on redeem; turns, confirmations, limits, notifications and reminders run as the link's user; server-level app settings when hosted; verify with `backend/tests/slack/` additions for two users

## 13. Isolation suite and end-to-end check (wave 5; needs 7–12)

- [ ] 13.1 `backend/tests/security/test_tenant_isolation.py`, adapted from `git show 301fba76^:backend/tests/security/test_tenant_isolation.py`, enumerating routes from the app and failing on any owned route without a case; verify it passes in hermetic CI
- [ ] 13.2 Two-browser check with `./launch.sh --hosted` as `alice` and `bob`: projects, chats, files, settings, skills isolated (hands-on, not in PR CI)

## 14. Docs and desktop regression (wave 5; needs 1–12)

- [ ] 14.1 AGENTS.md (identity, ownership helpers, hosted flag rules) and a `docs/hosted.md` listed or excluded in `docs/site.toml`; verify with `./scripts/ci-local.sh docs`
- [ ] 14.2 Desktop regression: upgrade a pre-change `vista.db`, and run `scripts/build_local_package.sh` smoke test; verify the researcher sees unchanged data and no reset prompt (hands-on)
