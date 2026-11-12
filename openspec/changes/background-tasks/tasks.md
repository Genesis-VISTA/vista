# Tasks

Tests marked **(sandbox)** need a real sandbox and stay out of PR CI
(`-m "not live and not hpc and not sandbox"`). Everything else is hermetic.

## 1. Sandbox: commands that outlive their call

- [x] 1.1 Record the microsandbox spike's result in `design.md` Decision 5 and adjust the start and stop commands to it; verified: the decision names `setsid -f` with redirected descriptors on an exec without a terminal, and `kill -TERM -- -<pgid>` from a second exec
- [x] 1.2 Add the background path to `run_bash` in `dev_mcp_server/server.py` (`background`, `task_id`, `log_path`): start with `setsid -f` and redirected descriptors on an exec without a terminal, capped log, `.pid` and `.exit` files in `/mnt/data/tasks/`, return without reading output; verify with a new `mcp_servers/dev_mcp_server/tests/test_background_bash.py` against `UnSandbox` (start returns at once, log grows, exit status 7 lands in `.exit`, foreground call unchanged)
- [x] 1.3 Cap the log at 10 MB with the cut-off line while the command runs on; verify in `test_background_bash.py` with a small cap injected through settings
- [x] 1.4 Add `wait_task(id, max_wait)` and `signal_task(id)`; verify in `test_background_bash.py` that `wait_task` returns on exit, on `max_wait`, and when a signalled task's group is gone with no `.exit`, and that `signal_task` ends a command with child and grandchild processes and leaves a second command running
- [x] 1.5 **(sandbox)** Repeat the stop and outlive-the-call cases against the real microsandbox and container backends in `tests/test_sandbox_live.py`; verify by running that file with `-m sandbox` on a host with each runtime

## 2. Inbox core

- [x] 2.1 Add the `inbox_item` table and the `auto_turns` and `quiet` columns on `chat_session` (additive, server defaults); verify `db/schema_check.py` passes against a database created before the change, in `backend/tests/test_schema_check.py`
- [x] 2.2 Add `services/inbox.py` (`put`, `edit`, `withdraw`, `take_queued`, `on_put`); verify with a new `backend/tests/test_inbox.py` (arrival order, edit and withdraw before take, both refused after)
- [x] 2.3 Add the per-run capability that takes queued items between steps and enqueues them on the run context, and pass it from `ProjectAgent.run_stream`; verify with a new `backend/tests/test_inbox_delivery.py` using a `FunctionModel` that an item put during a tool call is in the next model request, in arrival order
- [x] 2.4 Give `ChatRun` an origin and an `inbox_taken` event, keep those events through `compact_events`, and have the registry start a turn from the inbox when a run ends or an item arrives at an idle conversation; verify in `backend/tests/test_chat_run.py` that a `task_event` put to an idle conversation produces a saved turn whose history begins with it, and one put mid-run appears in that run's history
- [x] 2.5 Apply the hold-back rule (design Decision 3): cap of 10, reset on a researcher message, `wakes=false` items, and the derived "needs you" in `live_status` / `row_status`; verify in `backend/tests/test_inbox.py` (eleventh result waits and is delivered ahead of the next message) and `backend/tests/test_chat_runs.py` (status)
- [x] 2.6 Make Stop and the interrupted-run sweep set `quiet` and withdraw untaken messages; verify in `backend/tests/test_chat_run.py` that a result arriving after Stop starts no turn and is delivered with the next message

## 3. Sending through the inbox

- [x] 3.1 Route the send endpoint through the inbox so a send to a busy conversation is queued, and add the list, edit and delete routes for inbox items; verify in `backend/tests/test_chat_run_api.py` (busy send returns the queued item, no second run; edit after take answers 409)
- [x] 3.2 Announce queue changes on the run's event stream (`inbox_changed`); verify in `backend/tests/test_chat_run_api.py` that two subscribers both receive it
- [x] 3.3 UI: keep the message box enabled during a run, show queued messages as chips with edit and delete, and put withdrawn text back in the box after Stop; verify with a new `ui/tests/message-queue.test.tsx` and by updating `ui/tests/refused-send.test.ts`
- [x] 3.4 UI: draw `inbox_taken` messages as researcher bubbles mid-turn and `task_event` items as the expandable event row; verify in `ui/tests/run-renderer.test.ts`

## 4. Background commands in the backend

- [x] 4.1 Add the `background_task` table and `services/background_tasks.py` with its registry; verify with a new `backend/tests/test_background_tasks.py` (create, state changes, limit of 4 refused with the limit named)
- [x] 4.2 Intercept background `run_bash` calls in the agent's `process_tool_call` wrapper (limit, record, id and log path, 24-hour ceiling) and hide `wait_task` / `signal_task` from the agent's tools; verify in `backend/tests/test_project_agent_tools.py`
- [x] 4.3 Add the command watcher: hold the pooled agent, loop on `wait_task`, read the log tail, record the end, put the `task_event`, enforce the time limit; verify in `backend/tests/test_background_tasks.py` with a fake dev server (exit 7 reported, timeout recorded as timed out, lost server recorded as interrupted)
- [x] 4.4 Add the `list_tasks` and `stop_task` agent tools; verify in `backend/tests/test_project_agent_tools.py` that both answer from records after `invalidate_agents` rebuilds the agent, and that the running command still delivers its result
- [x] 4.5 **(sandbox)** End to end with a real sandbox: a background `sleep` started in one turn starts a second turn when it exits; verify with a new `backend/tests/test_background_tasks_live.py`

## 5. Job watches

- [ ] 5.1 Make expired or missing credentials and HTTP 429 (with `Retry-After`) recognisable in `get_hpc_job_status` failures; verify in `mcp_servers/vista_mcp_server/tests/test_job_status_errors.py` with the fake IRI and Globus clients
- [ ] 5.2 Add the `watch_hpc_job` agent tool and the watch task on its own vista MCP connection (limit of 8, metadata from Settings at each poll); verify in a new `backend/tests/test_job_watch.py` with an injected status reader
- [ ] 5.3 Events and pace: started once, ended once, failure believed on the second reading, 30 s within the two 10-minute windows then doubling to 300 s, wait honoured on 429; verify in `backend/tests/test_job_watch.py` with a fake clock
- [ ] 5.4 Pause on lost access, retry every 300 s and on a Settings save, resume without an agent turn, and show "needs you" while paused; verify in `backend/tests/test_job_watch.py`
- [ ] 5.5 Decide the open question in `design.md` (full status call or state-only reading between events) from what one reading costs, and record the decision there; verify the Open Questions section is empty or removed

## 6. Restart and deletion

- [ ] 6.1 At startup mark running commands interrupted with a `wakes=false` note, restart watches with an immediate reading, then let inboxes start turns; verify in `backend/tests/test_background_tasks.py` and `backend/tests/test_job_watch.py` (job finished while closed is reported; interrupted command starts no turn)
- [ ] 6.2 Make deleting a conversation with live tasks answer with their list unless confirmed, and on confirmation stop them and remove their logs; verify in `backend/tests/test_chat_run_api.py`

## 7. Background tab

- [ ] 7.1 Add `GET /chat/{id}/tasks`, the stop route and the log-tail route; verify in a new `backend/tests/test_background_tasks_api.py` (another conversation's tasks are not listed)
- [ ] 7.2 UI: add `BackgroundPanel` as a workspace tab shown once the conversation has a task, with state, advancing runtime, log link, stop control, the paused-watch line and the running count on the label; verify with a new `ui/tests/BackgroundPanel.test.tsx`
- [ ] 7.3 UI: show the live tasks in the delete-conversation dialog; verify with a new case in the chat list's test

## 8. Agent guidance, docs and whole-change checks

- [ ] 8.1 Describe background runs in the `run_bash` description and the three tools in `agents/base_system_prompt.md`, including "report every final state" for watches; verify in `backend/tests/test_project_agent_prompt.py`
- [ ] 8.2 Add a "Background tasks" section to `AGENTS.md` and a page under `docs/`, listed or excluded in `docs/site.toml`; verify `./scripts/ci-local.sh docs` passes
- [ ] 8.3 Run `./scripts/ci-local.sh` (all lint and tests) outside the checkout's `.env` and `openspec validate background-tasks --strict`; verify both pass
- [ ] 8.4 **(live)** Try it in the running app: start a background command, send a message while a turn runs and edit it, stop a turn with a task running, watch a dry-run job to its end; verify each against the scenarios in `specs/`
