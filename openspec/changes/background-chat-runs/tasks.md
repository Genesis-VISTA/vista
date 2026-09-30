## 1. Backend: run state and partial history

- [x] 1.1 Add `run_state` (default `idle`), `run_unseen` (default false) and `run_events` (nullable JSON) to `ChatSessionBase` in `backend/src/vista_backend/db/schemas.py`, and an optional `run_id` to `ChatTranscriptMessage`. Verify `init_db` adds the columns to an existing database by extending `backend/tests/test_migrate_columns.py`.
- [x] 1.2 Write the D3 trim as a pure function: given prior history and captured messages, return the completed steps plus a closing text part. Verify with unit tests covering a dangling tool call, a completed tool round-trip, and a text-only turn.
- [x] 1.3 Wrap `run_stream_events` in `capture_run_messages()` inside `ProjectAgent.run_stream` (`agents/agents.py`) and expose the captured messages when the run is cancelled or fails. Verify with a `FunctionModel` test that cancels mid-tool and still sees the completed tool call. If it can't, switch to `agent.iter()` per design D3 and note it here.

## 2. Backend: run registry

- [x] 2.1 Add `backend/src/vista_backend/services/chat_run.py`:
  - `ChatRun` (task, seq'd event list, status, pending prompts, condition) and a registry keyed by `chat_session_id`;
  - the run task holds the `project_agent_pool` lease and its own session from a module-level factory;
  - `run_started` and `run_finished` events.

  Verify with unit tests that one run can be started, subscribed to and awaited.
- [x] 2.2 Persist on completion: history (prior history plus new messages), `run_state`, `run_unseen = true`, and compacted `run_events` (design D7: parts collapsed, prompt events dropped). Verify that a test reads the row back and finds no `PartDeltaEvent` and no prompt events.
- [x] 2.3 Implement `stop(chat_session_id, reason)`: cancel the task, save the 1.2 trimmed history, and set state to `stopped` or `interrupted`. Verify with a test that stops a run after a completed tool call and checks the saved history.
- [x] 2.4 Refuse a second start while a run is active, and allow parallel runs across conversations. Verify with tests for both.

## 3. Backend: prompts that wait

- [x] 3.1 Remove the 5-minute `wait_for` on elicitations and tool approvals in `agents/agents.py`, so cancellation releases the waiting future. Verify with a test that a pending approval outlives a mocked clock advance and that Stop ends it.
- [x] 3.2 Raise `get_vista_mcp_server(read_timeout=...)` to 24 h, leaving `dev_mcp_server` as is. Verify with a unit assertion on the constructed server's timeout.
- [x] 3.3 Track pending prompts in the registry. Make `resolve_elicitation` append `prompt_resolved`, and have replay skip resolved prompts. Verify with tests that a replay shows only unanswered prompts, and that a second subscriber sees `prompt_resolved`.

## 4. Backend: API and lifespan

- [x] 4.1 Rework `POST /projects/{p}/agent/run` in `api/agent.py`:
  - streaming requires `chat_session_id`, starts through the registry and subscribes from seq 0, and answers 409 when busy;
  - non-streaming starts and awaits through the registry.

  Verify with the existing `backend/tests/test_agent_api.py`, updated for these changes.
- [x] 4.2 Add `GET …/chat-sessions/{id}/run/events?after=N` (SSE with `id:` set to seq; 204 when idle), `POST …/chat-sessions/{id}/run/stop`, and `GET /projects/{p}/chat-runs/status`. Verify with API tests.
- [x] 4.3 In `api/chat_sessions.py`, return `run_status`, `run_unseen` and `run_events` from the chat-session GET. `PUT` ignores `message_history` and accepts `ack_run` to clear the unseen flag and the events. Verify by updating `backend/tests/test_chat_sessions.py`.
- [x] 4.4 Lifespan in `api/api.py`: at startup, sweep `running` rows to `interrupted` and unseen. At shutdown, run `stop_all(reason="interrupted")` with a ~5 s bound before `project_agent_pool.clear()`. Verify with a test that starts the app over a DB holding a `running` row.

## 5. Backend: acceptance tests

- [ ] 5.1 Add `backend/tests/test_chat_runs.py` (unit/integration, hermetic, `FunctionModel`), covering the spec scenarios end to end through the API:
  - a disconnect mid-run completes and saves;
  - re-attaching replays from the start;
  - a send while busy returns 409;
  - a stop after a tool call keeps it in history;
  - an answered prompt is not replayed;
  - the unseen flag survives an app restart.

  Verify with `cd backend && uv run --extra dev pytest tests/test_chat_runs.py`.

## 6. UI: proxy routes and data

- [ ] 6.1 Read the route-handler and streaming docs in `ui/node_modules/next/dist/docs/`. Then add Next proxy routes for run events (streamed through), stop and status under `ui/app/api/chat/`, following `ui/app/api/chat/route.ts`. Verify with `npm run lint` and a hermetic stub fixture for each route.
- [ ] 6.2 Add a `useChatRunStatus(projectName)` hook in `ui/lib/`. It polls the status route every 3 s while the document is visible, and is shared by the conversation list and the nav rail. Verify with a vitest in `ui/tests/`.

## 7. UI: chat page

- [ ] 7.1 Move the SSE parsing and event dispatch out of the send closure in `ui/app/page.tsx` into a reusable renderer that tags bubbles with `run_id`. Verify the existing `ui/e2e-hermetic/chat.spec.ts` still passes.
- [ ] 7.2 Give the send fetch and the re-attach stream an `AbortController`, aborted on conversation switch and unmount. Stop writing `messageHistory` in the save effect. Verify that a hermetic test switching conversations mid-run shows no events in the wrong thread.
- [ ] 7.3 When a conversation opens:
  - if a run is active, remove that run's bubbles and re-attach from 0;
  - otherwise, if `run_events` is present, replay them, save with `ack_run`, and clear the dot;
  - otherwise, if the conversation is unseen, save with `ack_run`.

  Verify with a hermetic test: navigate to Skills and back, and the answer is drawn once.
- [ ] 7.4 Disable input while a run is active and show a Stop button that calls the stop route. Show a stopped turn as "Stopped" and an interrupted turn as "Interrupted when VISTA quit". Handle a 409 on send by re-attaching. Verify with a hermetic test.
- [ ] 7.5 Re-show a still-pending prompt on re-attach, and clear it on `prompt_resolved`, for form elicitation (the SSH login modal), URL elicitation and tool approval. Verify with a hermetic test driving a Lux-style SSH login form (username plus password fields) that appears after navigating back.

## 8. UI: status dots and nav rail

- [ ] 8.1 Add the conversation-list dots:
  - amber glowing (working);
  - amber with a ring (needs you);
  - green (done, unseen);
  - red (failed or interrupted, unseen).

  Use the existing tokens in `ui/app/globals.css`, and keep the glow still under `prefers-reduced-motion`. Verify with a hermetic test per state, using stubbed status responses.
- [ ] 8.2 Add the summary dot on the Chat entry in `ui/components/NavRail.tsx`, with urgency order needs you, then failed or interrupted, then done. When exactly one conversation needs you, activating Chat opens it; otherwise it opens the list. Never navigate on a status change. Verify with a hermetic test from the Skills page.

## 9. Verification

- [ ] 9.1 Run `./scripts/ci-local.sh` (lint and test for backend, ui and mcp) and confirm it is green.
- [ ] 9.2 **Manual, not in PR CI (live):** on the desktop stack (`./launch.sh logs --electron`):
  1. Send a prompt that runs a few tools, navigate to Skills, return, and see the green dot and the answer.
  2. Start a Lux submission, leave before the SSH login appears, come back more than 5 minutes later, log in, and confirm the job submits.
  3. Stop a run after a tool call and check the agent's next answer knows what already ran.
  4. Quit VISTA mid-run and see the red interrupted dot after relaunch.

  Record the results in the MR.
