# Tasks

## 1. Shared run path

- [x] 1.1 Add `services/conversation.start_turn` and route `api/agent.py` through it; existing `backend/tests/test_agent_api*.py` and chat-run tests pass unchanged
- [x] 1.2 Add `channel` to `ChatRun` and `ProjectAgent.run_stream`, reset per run; test that a pooled agent serves an app run after a Slack run with app behaviour

## 2. Data and secrets

- [x] 2.1 Add tables `slack_config`, `slack_link`, `slack_link_code`, `slack_thread`, `slack_pending`, `slack_detail`, `slack_reminder`, `slack_audit`, `hpc_job_record` and `chat_session.origin`; `init_db` on an existing DB adds them (test in `backend/tests/slack/test_schema.py`)
- [x] 2.2 `utils/secret_store.py` over `keyring`, refusing fail/null backends; tests with an in-memory keyring backend
- [x] 2.3 Per-install encryption key in the keyring with one-time re-encryption from the dummy key; `backend/tests/test_encryption_key.py`
- [x] 2.4 `SlackSettings` in `config.py` and `api/slack.py` config endpoints with write-only tokens; API tests

## 3. Access

- [x] 3.1 Workspace/channel allowlist and link lookup middleware; `backend/tests/slack/test_access.py` (unlinked refused, rate-limited refusal, foreign workspace ignored, channel allowlist)
- [x] 3.2 Link codes from DM and `POST /slack/links`; tests for expiry and wrong code

## 4. Transport and relay

- [x] 4.1 `SlackTransport`, `FakeTransport` (tests/slack/fakes.py) and Bolt Socket Mode implementation; a unit test that the Bolt app acks and dispatches a fake event
- [x] 4.2 Thread→conversation mapping and per-thread queue; `backend/tests/slack/test_threads.py`
- [x] 4.3 `RunRelay` progress message with throttled edits and transcript writes; tests against a scripted `ChatRun`
- [x] 4.4 `slack/format.py` mrkdwn, summary-first, chunking, fallbacks, Show more; `backend/tests/slack/test_format.py`

## 5. Confirmation and safeguards

- [x] 5.1 `SlackGateCapability`, form decline in Slack runs, read-only tags on native tools; tests that read-only runs and others raise approval
- [x] 5.2 Block Kit confirmation, requester-only, 10-minute expiry, recheck on Confirm; `backend/tests/slack/test_confirm.py`
- [ ] 5.3 Limits (nodes, walltime, jobs/day) and lock/unlock; tests in `test_gate.py`
- [ ] 5.4 `offer_choices` buttons; tests

## 6. HPC tools and jobs

- [ ] 6.1 MCP `preview_hpc_job` and `get_hpc_queue_status` (read-only) with hermetic tests in `mcp_servers/vista_mcp_server/tests/`
- [ ] 6.2 Record jobs on successful submit for every channel; `list_my_recent_jobs`; `backend/tests/slack/test_job_refs.py`
- [ ] 6.3 `JobMonitor` and notifications (DM or in-thread mention, desktop-jobs setting, offline catch-up, once only); `backend/tests/slack/test_job_monitor.py`

## 7. Presence

- [ ] 7.1 Lease in Slack, takeover, foreign-lease disconnect; `backend/tests/slack/test_lease.py`
- [ ] 7.2 Offline/online notices with 2-minute debounce, shutdown hook; `backend/tests/slack/test_notices.py`
- [ ] 7.3 Sleep notifications and sleep prevention per OS (`slack/power.py`), optional; unit tests for command construction only
- [ ] 7.4 Credential reminders at 24 h and 2 h, post-expiry refusal; `backend/tests/slack/test_credential_reminders.py`
- [ ] 7.5 App Home view; snapshot test of the blocks
- [ ] 7.6 Audit log with redaction; `backend/tests/slack/test_audit.py`
- [ ] 7.7 Lifespan wiring: nothing starts with Slack off (test)

## 8. Desktop

- [ ] 8.1 Settings → Slack section (tokens, allowlists, links, limits, lock, notifications, handle here, sleep, background) and Slack badge; `npm run lint` and colour check pass
- [ ] 8.2 Launcher `--service` with `service.json`; `./scripts/ci-local.sh launcher`
- [ ] 8.3 Login item install/remove (`services/background_service.py`) for launchd, systemd, schtasks; unit tests of generated files and commands
- [ ] 8.4 Electron attaches to a running service; `cd electron && npm test`
- [ ] 8.5 Installers stop/restart the service; `./scripts/ci-local.sh install`

## 9. Docs and verification

- [ ] 9.1 `backend/src/vista_backend/slack/manifest.yaml`, `docs/slack.md`, `docs/slack-relay.md`; manifest parses as YAML in a test
- [ ] 9.2 Full `./scripts/ci-local.sh` passes
- [ ] 9.3 Manual mobile checklist (iOS, Android) — live, not in PR CI
