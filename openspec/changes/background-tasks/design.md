## Context

See `proposal.md` for motivation. The facts below were checked in the code before designing.

- `run_bash` (`mcp_servers/dev_mcp_server/src/dev_mcp_server/server.py`) reads a command's
  output to EOF before returning. `Sandbox.exec` already returns a live process, but
  `SandboxProcess` has no `kill`, the container backend's `exec` is a host-side
  `<runtime> exec -i` client, and no backend puts a command in its own process group.
- Each conversation has its own `ProjectAgent`, dev MCP server process and sandbox (pool key
  `(chat_session_id, project_id)`); all of a project's conversations share one volume, mounted
  at `/mnt`. The Files page lists only `data/output` and `data/uploads`.
- `TTLPool.delete` removes a held entry from the map at once and defers its cleanup until it is
  released. So after a Settings or model change the next turn builds a second agent while a
  holder keeps the first one, and its sandbox, alive.
- One MCP connection carries concurrent requests; the 1860 s read timeout applies per request.
  `direct_call_tool` bypasses `process_tool_call`, so a call outside a run attaches its own
  metadata. While a server-initiated request (an elicitation) is unanswered, no other response
  on that connection is delivered.
- Only `ChatRunRegistry.start`, called from the send endpoint, starts a turn. A send to a busy
  conversation raises `RunBusy` (409).
- PydanticAI 1.105.0: `RunContext.enqueue` adds messages to a live run and may be called from a
  capability hook; its drain capability is installed automatically. `run_stream_events` does
  not expose the `AgentRun`, and accepts `capabilities=`.
- `CampaignMonitor` is tied to `hpc_job` rows that require a campaign step, and is off by
  default. `agents/campaign/mcp_invoke.py` already calls an HPC tool outside a run with the
  Settings credentials attached, and `wiring.parse_job_state` / `campaign_monitor.normalize_state`
  read the status text.
- On current main all four clusters are read with saved tokens; Lux no longer needs an SSH
  login.
- The OLCF documentation states no rate limit for S3M's job-status call, and VISTA has no
  handling for HTTP 429 today.

## Goals / Non-Goals

**Goals:**

- One delivery mechanism for researcher messages and task results.
- Background commands with no polling: the backend waits on the process.
- Job watches that never touch campaign mode's tables or loop.
- Every schema change additive, so no install is asked to reset its data.

**Non-Goals:**

- Generalising or replacing `CampaignMonitor`.
- Sharing one sandbox across agent rebuilds.
- Moving a foreground `run_bash` to the background after it has started.

## Decisions

### 1. The inbox is a table and a small service, and the run pulls from it

`inbox_item` (id, chat_session_id FK cascade, kind `message` | `task_event`, text, payload
JSON, state `queued` | `taken` | `withdrawn`, wakes bool, created_at, taken_at) plus
`services/inbox.py` with `put`, `edit`, `withdraw`, `take_queued` and `on_put`.

Delivery into a running turn is a pull, not a push: `run_stream` passes a per-run capability
whose hook runs between the agent's steps, calls `take_queued` and hands the items to
`ctx.enqueue(..., priority="asap")`. Taking and marking `taken` happen together in that hook,
which makes "editable until taken" exact and removes the race of pushing into a run that has
just ended.

When a run ends, `ChatRunRegistry` asks the inbox whether anything is still queued and, if the
conversation may start a turn (Decision 3), starts one. `on_put` does the same for an idle
conversation.

*Alternative: push with `enqueue` from the watcher.* Rejected: an item pushed after the last
drain raises `UndrainedPendingMessagesError` or is lost, and the moment a message stops being
editable would be unknowable. *Alternative: keep the queue in the UI.* Rejected: task results
come from the backend, a second view must show the same queue, and results must survive a
restart.

### 2. A send always goes through the inbox

The send endpoint puts a `message` item and returns it with whether a run was started. The
`RunBusy` 409 goes away for sends; `ChatRunRegistry.start` keeps raising it internally as a
guard. New routes: `PATCH` and `DELETE` on an inbox item (409 once taken) and `GET` for the
queued items, which the run's event stream also announces (`inbox_changed`) so open views stay
in step.

A run records its origin (`researcher` or `task`) and emits an `inbox_taken` event for each
item delivered, with its kind and text. The UI draws a `message` as a researcher bubble and a
`task_event` as the event row; compaction keeps these events so a turn nobody watched is drawn
the same way.

### 3. Three things can hold a conversation back, all on the chat row

Additive columns on `chat_session`: `auto_turns` (int, default 0) and `quiet` (bool, default
false). A turn may be started by the inbox only if the conversation is idle and either a
queued item is a researcher `message`, or some queued item has `wakes=true`, `quiet` is false
and `auto_turns < 10`.

- A turn of origin `task` increments `auto_turns`; a researcher message resets it to 0 and
  clears `quiet`.
- Stop, and the startup sweep for interrupted runs, set `quiet` and withdraw untaken `message`
  items, whose text the UI puts back in the message box.
- An interrupted command's note is put with `wakes=false`.

"Needs you" for these cases is derived, not stored: queued `task_event` items in an idle
conversation, or a paused watch. `row_status` and `live_status` gain that branch.

### 4. Task records live in the backend

`background_task` (id, chat_session_id FK cascade, project_id, label such as `bg-3`, kind
`command` | `job_watch`, state, command, job_id, cluster, log_path, timeout_s, exit_status,
last_job_state, started_reported bool, paused_reason, next_poll_at, created_at, ended_at).
States: `running`, `exited`, `stopped`, `timed_out`, `interrupted`, and for watches `watching`,
`paused`, `ended`.

`services/background_tasks.py` holds a registry of one asyncio task per live record, modelled
on `ChatRunRegistry`. `list_tasks`, `stop_task` and `watch_hpc_job` are backend function tools
on the agent, so they answer from these records whichever sandbox the current agent owns.

*Alternative: keep the list in the dev MCP server.* Rejected: after an agent rebuild the new
server knows nothing of the old one's tasks.

### 5. Background commands: the backend intercepts, the sandbox runs, the watcher waits

The agent calls the MCP tool `run_bash(command, background=true, timeout=...)`. The agent's
existing `process_tool_call` wrapper sees the background flag and, before forwarding, checks
the limit of 4, creates the record, and adds the task id and log path to the call. Refusals
come back as tool errors.

In the dev MCP server the background path starts, without reading its output:

```
setsid -f bash -c '<wrapper>' </dev/null >/dev/null 2>&1
```

on an exec without a terminal (`combine_streams=False`). The wrapper writes its own pid, which
`setsid` makes the session and group id, to `<id>.pid`; runs

```
bash -c '<command>' 2>&1 | { head -c $CAP > LOG; echo "[output cut off at 10 MB]" >> LOG; cat > /dev/null; }
```

and writes the command's own status (`PIPESTATUS[0]`) to `<id>.exit`; all three files sit in
`/mnt/data/tasks/`. `head` bounds the log; the trailing `cat` keeps the command from dying of
a closed pipe; the cut-off line is written only if `head` reached the cap.

The shape of the start comes from a spike against real microsandbox 0.7.2 sandboxes:

- Under the terminal that `run_bash` uses for combined output, a plain `setsid bash -c ...`
  is killed the moment its exec ends, and so are `nohup ... &` and `setsid ... &`. `setsid -f`
  with all three descriptors redirected survives, and so does any `setsid` on an exec without
  a terminal.
- Dropping the host-side handle of a plain exec kills its command. A `setsid -f` task with
  its descriptors redirected survives the drop, and a later exec can see and signal it.
- Without a terminal an exec does not report its exit until every process that inherited its
  output pipe is gone, which is why the descriptors are redirected and not left attached.
- `kill -TERM -- -<pgid>` from a second exec ended every descendant, grandchildren included,
  and did not touch a concurrent command in another group. `KILL` was never needed.
- A group signal also kills the wrapper, so `<id>.exit` is never written for a stopped task.
- The handle's own `kill()` ends a plain exec's whole tree but only the wrapper of a detached
  one, so it is not used.
- `sandbox.close()` returned in under a second with tasks running and left nothing behind.

Two dev MCP tools are hidden from the agent by the existing tool filter:

- `wait_task(id, max_wait)` returns when `<id>.exit` appears, when the task's group has no
  process left and no `.exit` was written (it was signalled), or after `max_wait` (25 minutes,
  under the per-request timeout), saying which.
- `signal_task(id)` runs, as a second exec, `kill -TERM -- -<pgid>`, waits a few seconds, then
  `kill -KILL -- -<pgid>`.

The watcher task holds `project_agent_pool.get(key)` for the life of the command, which keeps
that agent and sandbox alive across idle expiry and across a rebuild (see Context), and loops
on `wait_task` through `direct_call_tool`. On exit it reads the tail of the log from the host
side of the volume, updates the record and puts a `task_event`. The time limit is the
watcher's: at the deadline it calls `signal_task` and records `timed_out`. `stop_task` and the
tab's stop control go through the same watcher, so they reach the sandbox that owns the
process.

*Alternative: the backend watches for the `.exit` file on the host.* Kept as the fallback if a
held call proves fragile; it needs no MCP call but is polling. *Alternative: a server-pushed
notification.* Rejected: FastMCP has no request context to send from after the tool returns.

The spike covered the microsandbox backend only. The container backend runs every exec
without a terminal, the case that behaved simply above, but it has not been run; task 1.5
checks both. Whether a task ended by a signal is `stopped` or `timed_out` is the watcher's
knowledge, since it sent the signal; a group that vanishes with no `.exit` and no signal from
the watcher is recorded as `interrupted`.

### 6. Job watches: one task per watch, on a connection of their own

`watch_hpc_job` checks the limit of 8, creates a `job_watch` record and starts its task. The
task reads the job with `get_hpc_job_status` through one shared, callback-free connection to
the vista MCP server (as `mcp_invoke.build_mcp_invoke` does), attaching metadata built from
Settings as read at each poll, so a reconnected credential applies to the next reading. A
connection of its own keeps a watch from stalling behind an unanswered prompt in a turn.

- **Events.** First reading in a running state puts a "started" `task_event` (once,
  `started_reported`). A final state puts an "ended" one and ends the watch. A failure is
  acted on only when the previous reading was also a failure, the rule `CampaignMonitor`
  learned on Odo.
- **Pace.** 30 s while within 10 minutes of the watch starting or of the job starting to run;
  otherwise the interval doubles from 30 s up to 300 s. `next_poll_at` is saved.
- **Too many requests.** The IRI client maps HTTP 429 to a typed error carrying
  `Retry-After`; the tool reports it in a form the watcher recognises, and the watcher waits
  at least that long, 300 s when no value is given.
- **Lost access.** A missing token, an expired token or `GlobusSessionExpired` pauses the
  watch with the cluster's name. A paused watch retries every 300 s and on every Settings
  save, and resumes on the first reading that works.
- **Restart.** At startup every `watching` or `paused` record gets its task back, and its
  first reading is immediate.

Watches never read or write `hpc_job`; the note in `config.py` about a second poller racing
the campaign monitor on those rows does not apply.

### 7. Startup and deletion

Startup, after the existing interrupted-run sweep: mark `running` commands `interrupted` and
put a `wakes=false` note for each; restart watches; then let each conversation's inbox start
a turn if Decision 3 allows.

Deleting a conversation: the delete endpoint refuses with the list of live tasks unless the
request confirms; on confirmation it stops them through the registry before the row, and with
it the cascade, goes. Its logs under `data/tasks/` are removed by task id.

### 8. UI

- The message box stays enabled during a run. Queued messages are chips above it with edit
  and delete, driven by `inbox_changed`.
- `BackgroundPanel` is a fourth workspace tab, shown once the conversation has a task, fed by
  `GET /chat/{id}/tasks` polled every 3 s while the tab is open and refreshed on run events.
  The log link opens the existing file preview on a tail endpoint for the task's log.
- The event row is a new renderer in the run renderer for `inbox_taken` items of kind
  `task_event`.
- The delete dialog shows the live tasks the endpoint returned.

## Risks / Trade-offs

- [A self-started turn spends a model call nobody asked for] → cap of 10 in a row, quiet after
  Stop, no wake for interrupted commands or paused watches.
- [An old sandbox lives on after a rebuild for as long as its command runs] → bounded by the
  24-hour limit and by 4 commands per conversation.
- [Two agents exist for one conversation after a rebuild; a new background command starts in
  the new sandbox while an old one finishes in the old] → records carry which agent owns each
  task; logs share the volume, so both are readable from either.
- [`RunContext.enqueue`'s queue fields are marked internal in PydanticAI] → the hook is the
  only code that touches it; `backend/tests/test_inbox_delivery.py` pins the behaviour with a
  `FunctionModel`, so an upgrade that changes it fails in PR CI.
- [A held `wait_task` call is lost if the dev MCP process dies] → the watcher treats a failed
  call as the sandbox being gone and records `interrupted`.
- [Undocumented S3M rate limits] → 429 handling and backoff; one reading per watch per
  interval, at most 8 watches per conversation.
- [A message taken at the moment the researcher edits it] → the edit is refused with 409 and
  the view shows the message as delivered.

## Migration Plan

Two new tables and two columns with server defaults; `db/schema_check.py` passes on existing
installs. No data is rewritten. Rolling back to a build without the change leaves the new
tables and columns unused.

The UI and backend ship together in one package, so the changed send response has no
out-of-step client.

## Open Questions

- Whether the watcher should call `get_hpc_job_status` as it is, which also tails the job's
  log on OLCF clusters, or a lighter state-only reading for the polls between events. Either
  satisfies the specs; decide when the 429 handling is written, from what one reading costs.
