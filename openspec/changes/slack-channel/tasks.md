# Tasks

The rework of !163 onto main's conversation inbox. !163's original task list is in git history
(`openspec/changes/slack-channel/tasks.md` at `origin/slack-plugin`).

## 1. Onto main

- [x] 1.1 Merge `origin/main` into `slack-on-inbox` and resolve the conflicts in `agents/agents.py`, `api/agent.py`, `services/chat_run.py`, `submit_job_mcp.py`, `docs/site.toml` and `scripts/docs_site/test_sync_docs.py`, keeping main's inbox path in `api/agent.py`; backend, MCP and Slack tests pass (`./scripts/ci-local.sh backend mcp test`)

## 2. Laptop extras out

- [x] 2.1 Remove the lease (`slack/lease.py`), offline/online notices (`slack/notices.Notices`, keeping `CredentialReminder`), sleep (`slack/power.py`) and their settings, API fields, UI controls and tests; Slack still connects and answers in `test_service.py`
- [x] 2.2 Remove background mode: `services/background_service.py`, the launchers' `--service` and `service.json`, `electron/src/background-service.js` and its use in `main.js`, the installers' service stop/start, their tests and the Settings toggle; `./scripts/ci-local.sh electron install launcher` pass
- [x] 2.3 Remove the per-install database key (`encryption_key_in_secret_store`, `reencrypt_legacy_values`, the launchers' export) and `test_encryption_key.py`; `secret_store` keeps only the Slack tokens

## 3. Turns through the inbox

- [x] 3.1 Slack messages enter with `inbox.put` and `chat_run_registry.wake`; remove `services/conversation.start_turn`, `ThreadQueue`, `QUEUE_FULL` and `BUSY`; test: three messages sent during a running turn are all answered, in order, and a message waiting across a restart is answered after it
- [x] 3.2 `wake` takes the run's channel from `chat_session.origin` and passes it to `run_stream`; test that a self-started turn in a Slack conversation is a Slack turn and one in an app conversation is not
- [x] 3.3 The relay follows every run of a Slack conversation, self-started ones included (a registry hook on run start), and records each turn in the transcript once; test with a job-watch result
- [x] 3.4 React 👀 to a queued Slack message and ✅ when a turn takes it; add `reactions:write` to `manifest.yaml`; `FakeTransport` records reactions
- [x] 3.5 The waiting reaction is ⏳ `hourglass_flowing_sand`; test that waiting messages carry it, then ✅
- [x] 3.6 Ask for the project: `threads.conversation_for` raises `ChooseProject` instead of falling back to the first project; a `slack_project_ask` table; the picker (buttons, a menu past five) posted through `format`; an `Action` handler for `vista_project`; the held prompts go into the inbox in order; tests: several projects → asked, one → not, another user's tap refused, a reply before the tap held and answered after it, an ask that survives a restart

## 4. Read-only on the desktop

- [x] 4.1 The agent-run and prompt-answer endpoints refuse a Slack conversation with `409`; Stop still works; API tests
- [x] 4.2 The UI disables the message box of a Slack conversation with a note that it continues in Slack and shows its prompts without answer controls; component test

## 5. Confirmation and limits

- [x] 5.1 The confirmed set: `submit_hpc_job`, `agenthpc_submit_parameter_set`, `cancel_hpc_job` and the `agenthpc` cancels; `cancel_hpc_job` uses its card layout; tests in `test_gate.py`
- [x] 5.2 Parse approval arguments into a dict when the model sent them as a string, so the card, preview and recheck see them; test with string arguments (the blank-card bug)
- [x] 5.3 Check nodes and walltime against `preview_hpc_job`'s resolved values, for both submit tools; test the "defaults over the limit" scenario
- [x] 5.4 Confirm requires `action_id == CONFIRM`; tag `watch_hpc_job` and `list_tasks` read-only
- [x] 5.5 A form whose only field is a choice (a transfer's Approve/Decline) is shown in Slack as buttons, requester-only, 10-minute expiry; other forms and URLs are declined at once; tests in `test_choices.py`
- [x] 5.6 In a turn VISTA started, the card @mentions the conversation's Slack user
- [x] 5.7 `preview_hpc_job` shows the account an OLCF job is charged to: it introspects the S3M token (a few seconds at most, S3M only, never the cluster) instead of reading only a 10-minute cache, and falls back to "the S3M token's project" only when S3M does not answer; tests: the project from a stubbed introspection, the fallback on a failed or slow one

## 6. Job notices from watches

- [x] 6.1 A successful submission in a Slack conversation registers a job watch; its result starts a turn posted in the thread; test with a fake watch reader
- [x] 6.2 Remove `slack/jobs.JobMonitor`, `notify_desktop_jobs` and `notify_in_thread`, their UI and tests; keep `hpc_job_record` for `list_my_recent_jobs` and the daily limit

## 7. Review fixes

- [x] 7.1 `format.chunks` terminates on an unclosed code fence, and `_prose` on a stray NUL; escape `&`, `<`, `>` in code too; tests with those inputs
- [x] 7.2 Button taps in DMs pass the channel allowlist; test in `test_access.py`
- [x] 7.3 `SocketModeTransport.connect` records the socket only once open; a failed connect is reported and retried; test
- [x] 7.4 `stop()` and `close_stale` decline open cards' prompts; test that a settings restart with an open card leaves the run unblocked
- [x] 7.5 A card that cannot be posted declines its prompt, and a turn that fails replaces the progress message; tests in `test_relay.py`
- [x] 7.6 "Show more" expands the answer in place: `chat.update` to the summary plus as much detail as one message holds, without the button; only the remainder is posted, once; an edit Slack refuses falls back to posting; tests: a tap edits and removes the button, a second tap posts nothing, an answer past one message posts only its remainder
- [x] 7.7 The relay attaches the run's displayed files in the thread after the answer: `displayed_file` rows by run id, read from the output or uploads folder, uploaded with `transport.upload`; at most 10, then a line saying how many more are in the desktop app; a failed upload posts a line naming the file; tests in `test_relay.py`: a PNG is uploaded in the thread, 12 files upload 10 and say "2 more", a failed upload posts the fallback line, a desktop conversation uploads nothing

## 8. Docs

- [x] 8.1 `docs/slack.md`: the manifest inline (with a test that it matches `manifest.yaml`), `reactions:write`, the read-only desktop view, the confirmation rules, a note that some workspaces need an admin to approve new apps, one machine at a time; remove "Away from your desk" and the "Standing by" troubleshooting
- [x] 8.2 Mark `docs/slack-relay.md` superseded by the facility-service plan; list only `slack.md` in `docs/site.toml` (the relay note is excluded: a design note, not for the public site); `./scripts/ci-local.sh docs` passes
- [x] 8.3 A short Slack section in `AGENTS.md`: Slack enters through the inbox, a conversation's channel is its origin, the confirmed set and where to add a new cluster action

## 9. Verify

- [x] 9.1 Full `./scripts/ci-local.sh` passes
- [ ] 9.2 By hand on this Mac with Beam on Holly: a DM, a queued message, a submission with Confirm, its watch reported in the thread, a transfer approved by buttons, lock, and the read-only desktop view
- [ ] 9.3 Manual mobile checklist (iOS, Android) — live, not in PR CI
