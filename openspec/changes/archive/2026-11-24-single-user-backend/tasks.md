## 1. Backend: settings row

- [x] 1.1 Add `LocalSettingsTable`, `LocalSettingsUpdate` and `LocalSettingsPublic` (`db/schemas.py`) carrying the user fields, `install_id` and `display_name`; delete `UserTable`, `UserCreate`, `UserUpdate`, `UserSelfUpdate`, `UserPublic`, `UserPublicWithConfig` and `ProjectMemberTable`
- [x] 1.2 Add `services/local_settings.py` (get, update, `LocalSettingsDep`) with the write-only token rules and agent invalidation from `services/user.py`; delete `services/user.py`, `services/auth.py` and `ServiceUser`
- [x] 1.3 In `init_db`, create the settings row with an `install_id`; drop the test-user seeding from `db/seed.py`
- [x] 1.4 Replace `api/users.py` with `api/settings.py` (`/settings`, `/settings/inference`, `/settings/hpc-status`, `/settings/globus/{cluster}/*`), and remove the app-wide identity dependency and the member routes

## 2. Backend: no user identity

- [x] 2.1 Remove `user_id` from `chat_session`, `campaign_run`, `hpc_job` and `debate_run` and from every service and route that filtered or set it
- [x] 2.2 Key the agent pool by `(session_id, project_id)` and mount the sandbox volume at `volumes/{project_id}`
- [x] 2.3 Make agents, HPC tool metadata, `globus_auth`, `repo_tokens`, the campaign monitor, debates and inference resolution read the settings row
- [x] 2.4 Remove `user` parameters from project, file, report and skill services

## 3. UI

- [x] 3.1 Turn `lib/user.ts` into a settings client and move its proxy routes under `app/api/settings/`
- [x] 3.2 Remove `MembersModal` and the members calls, and the `x-amzn-oidc-*` forwarding in `app/api/_backend.ts`

## 4. Tests, scripts and docs

- [x] 4.1 Rewrite the user-referencing backend tests against the settings row; delete `test_auth_dev_identity.py`, `security/test_tenant_isolation.py` and the membership parts of `test_access_control.py`; add tests for the single row and the install id
- [x] 4.2 Remove `loadgen.py --users` and the identity header from `backend/scripts/`, and update `docs/evaluation-runbook.md` Phase D
- [x] 4.3 Run `./scripts/ci-local.sh backend ui mcp` (lint and test) and verify it passes
