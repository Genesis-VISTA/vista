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
(`InboxDelivery`) whose hook runs between the agent's steps, calls `take_queued` and hands the
items to `ctx.enqueue(..., priority="asap")`. Taking and marking `taken` happen together in
that hook, which makes "editable until taken" exact and removes the race of pushing into a run
that has just ended. PydanticAI's own drain is outermost and empties the queue in
`before_model_request` before our hook would see it, so the take runs in `before_node_run` of
each `ModelRequestNode`, and once more in `after_node_run` when the run would end (the drain's
end-of-run redirect runs after ours). If the database is locked, which a campaign tool's
flushed but uncommitted writes in the same turn can cause, that step delivers nothing and the
items wait for the next.

Each item is its own user prompt part. A researcher message taken mid-turn is preceded by a
note (`MID_TURN_NOTE`): "[The researcher sent this while you were working. Their earlier
request still stands unless this changes it.]". Bare, the model took it for a new request and
dropped the rest of the one it was answering; the base system prompt names the note too. The
thread draws the words sent, from `inbox_taken`, not the noted text.

When a run ends, `ChatRunRegistry.wake` asks the inbox whether anything is still queued and,
if the conversation may start a turn (Decision 3), starts one. It does so before the ending
run's `run_finished` is sent, which names the new run (`next_run_id`) so a watching view
follows on to it. `on_put` does the same for an idle conversation. A turn started from several
queued items takes them all, in arrival order, as one prompt joined by blank lines; the
`inbox_taken` events keep them apart for the UI.

*Alternative: push with `enqueue` from the watcher.* Rejected: an item pushed after the last
drain raises `UndrainedPendingMessagesError` or is lost, and the moment a message stops being
editable would be unknowable. *Alternative: keep the queue in the UI.* Rejected: task results
come from the backend, a second view must show the same queue, and results must survive a
restart.

### 2. A send always goes through the inbox

The send endpoint puts a `message` item without notifying the registry, then calls `wake`
itself, so a failure to start the turn reaches the request (the item is withdrawn). A started
turn is streamed as before; a send to a busy conversation answers 202 `{started: false,
item}`. The `RunBusy` 409 goes away for sends; `ChatRunRegistry.start` keeps raising it internally as a
guard. New routes: `PATCH` and `DELETE` on an inbox item (409 once taken) and `GET` for the
queued items, which the run's event stream also announces (`inbox_changed`) so open views stay
in step.

A run records its origin (`researcher` or `task`) and emits an `inbox_taken` event for each
item delivered, with its id, kind and text, also for the items a turn starts with. The UI
draws a `message` as a researcher bubble and a `task_event` as the event row, and drops the
item from its queue chips: a queue read in the moment between a send queueing its message and
the turn taking it would otherwise leave a chip for the whole turn. Compaction keeps
`inbox_taken` so a turn nobody watched is drawn the same way, and drops `inbox_changed`, since
a queue a finished turn saw is not the queue now.

### 3. Three things can hold a conversation back, all on the chat row

Additive columns on `chat_session`: `auto_turns` (int, default 0) and `quiet` (bool, default
false). A turn may be started by the inbox only if the conversation is idle and either a
queued item is a researcher `message`, or some queued item has `wakes=true`, `quiet` is false
and `auto_turns < 10`.

- A turn of origin `task` increments `auto_turns`; a researcher message resets it to 0 and
  clears `quiet`.
- Stop, and the startup sweep for interrupted runs, set `quiet` and withdraw untaken `message`
  items, whose text the UI puts back in the message box.
- An interrupted command's note is put with `wakes=false`: at startup, and whenever its
  sandbox is lost mid-command, which is most often VISTA quitting with its dev servers.

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
come back as the tool's answer, beginning "Refused:", so the agent can act on them without
spending a retry. The check and the insert run under a lock per conversation: a model step's
tool calls run concurrently, and five starts in one step otherwise all counted none running
and all took the label `bg-1`. Watches take the same lock. `task_id` and `log_path` are
removed from `run_bash`'s schema as the model sees it (a `prepared` toolset); `timeout` stays,
and the dev server accepts it without acting on it.

In the dev MCP server the background path starts, without reading its output:

```
setsid -f bash -c '<wrapper>' </dev/null >/dev/null 2>&1
```

on an exec without a terminal (`combine_streams=False`). The wrapper writes its own pid, which
`setsid` makes the session and group id, to `<id>.pid`; runs

```
bash -c '<command>' </dev/null 2>&1 | perl -e '<capper>' $CAP LOG '[output cut off at 10 MB]'
```

and writes the command's own status (`PIPESTATUS[0]`) to `<id>.exit`; all three files sit in
`/mnt/data/tasks/`. The capper reads with `sysread` and writes each chunk at once, stops
writing at the cap, adds the cut-off line only when output went past it, and keeps reading so
the command never dies of a closed pipe. `head -c` was the first choice and does not work: it
holds its output in a stdio buffer on both GNU coreutils (the sandbox image) and macOS, so a
running task's log stayed empty until the command ended. `perl` is in Ubuntu's `perl-base`
and on macOS. The start waits up to 10 s for `<id>.pid` before returning, so a stop or a wait
always has a group to act on; `wait_task` and `signal_task` refuse any pid that is not a
number above 1, so a bad file cannot signal every process.

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

The watcher task holds the agent that started the command for the life of the command, through
`TTLPool.hold(agent)`, which keeps that agent and sandbox alive across idle expiry and across
a rebuild (see Context), and loops on `wait_task` through `direct_call_tool`. Holding
`project_agent_pool.get(key)` would pin whatever agent the key maps to when the watcher
starts: a Settings change during the very turn that starts the command would leave the key
pointing at a new agent with an empty sandbox. `hold` finds the entry that holds this agent,
including one already deleted and waiting for its holders, and defers its cleanup until
released; the watcher takes it before the starting turn lets go. On exit it reads the tail of the log from the host
side of the volume, updates the record and puts a `task_event`. The time limit is the
watcher's: at the deadline it calls `signal_task` and records `timed_out`. `stop_task` and the
tab's stop control go through the same watcher, so they reach the sandbox that owns the
process.

*Alternative: the backend watches for the `.exit` file on the host.* Kept as the fallback if a
held call proves fragile; it needs no MCP call but is polling. *Alternative: a server-pushed
notification.* Rejected: FastMCP has no request context to send from after the tool returns.

The spike covered the microsandbox backend only. The container backend runs every exec
without a terminal, the case that behaved simply above; task 1.5 ran the start, stop,
outlive-the-call and close cases against both, and both passed. Whether a task ended by a signal is `stopped` or `timed_out` is the watcher's
knowledge, since it sent the signal; a group that vanishes with no `.exit` and no signal from
the watcher is recorded as `interrupted`.

### 6. Job watches: one task per watch, on a connection of their own

`watch_hpc_job` checks the limit of 8, creates a `job_watch` record and starts its task. The
task reads the job's state with `read_hpc_job_state`, a vista MCP tool the agent never sees,
through one shared, callback-free connection to the vista MCP server (as
`mcp_invoke.build_mcp_invoke` does), attaching metadata built from Settings as read at each
poll, so a reconnected credential applies to the next reading. A connection of its own keeps
a watch from stalling behind an unanswered prompt in a turn.

A reading is the job's state alone: one IRI status call, answered as `{state, exit_code?}` or
as a structured error (`rate_limited` with `retry_after`, `access_lost` with the cluster).
`get_hpc_job_status` also tails the job's log over Globus once the job runs, about four
facility calls per reading, and a lapsed Globus session would then hold back a result that
S3M can still give. The log and outputs are the agent's to fetch once told the job ended.

- **Events.** First reading in a running state puts a "started" `task_event` (once,
  `started_reported`). A final state puts an "ended" one and ends the watch. A failure is
  acted on only when the previous reading was also a failure, the rule `CampaignMonitor`
  learned on Odo.
- **Pace.** 30 s while within 10 minutes of the watch starting or of the job starting to run;
  otherwise the interval doubles from 30 s up to 300 s. `next_poll_at` is saved.
- **Too many requests.** The IRI client maps HTTP 429 to a typed error carrying
  `Retry-After` (`IriRateLimited`); the reading reports it as `rate_limited`, and the watcher
  waits at least that long, 300 s when no value is given. `get_hpc_job_status` raises the
  same typed error, so its message says to wait.
- **Lost access.** A missing or rejected S3M or NERSC IRI token (`MissingCredential`,
  `IriCredentialRejected`) pauses the watch with the cluster's name. Globus is never read by
  a watch, so a lapsed Globus session cannot pause one. A paused watch retries every 300 s and on every Settings
  save, and resumes on the first reading that works.
- **Restart.** At startup every `watching` or `paused` record gets its task back, and its
  first reading is immediate.

Watches never read or write `hpc_job`; the note in `config.py` about a second poller racing
the campaign monitor on those rows does not apply.

### 7. Startup and deletion

Startup, after the existing interrupted-run sweep: mark `running` commands `interrupted` and
put a `wakes=false` note for each; restart watches, each reading at once; then let each
conversation's inbox start a turn if Decision 3 allows. A turn started then takes every queued
item, so an interrupted command's note arrives with a job result that started the turn, ahead
of it, rather than waiting for the researcher's message. At a quit the watchers are cancelled
without recording anything, but the dev servers can die first, in which case a watcher records
the command `interrupted` itself; the note is the same quiet one (Decision 3).

Deleting a conversation: the delete endpoint refuses with the list of live tasks unless the
request confirms; on confirmation it sets the conversation `quiet`, so the results of the tasks
it stops start no turn, stops them through the registry before the row, and with it the
cascade, goes. Its commands' logs, `.pid` and `.exit` under `data/tasks/` are removed by task
id, also when every task had already ended.

### 8. UI

- The message box stays enabled during a run. Queued messages are chips above it with edit
  and delete, driven by `inbox_changed`.
- `BackgroundPanel` is a fourth workspace tab, shown once the conversation has a task, fed by
  `GET /chat/{id}/tasks` polled every 3 s while the tab is open, every 15 s while it is closed
  and something runs (for the running count on its label), and refreshed on run events.
  Like the Jobs panel it stays mounted, since it decides whether its tab exists. The log link
  opens the existing file preview on a tail endpoint for the task's log (its last 64 KiB),
  with a Refresh, since the preview reads a URL once. A paused watch's row says what it
  needs ("Paused: Reconnect Odo in Settings"); the conversation's "needs you" dot itself does
  not.
- A turn the page did not start (VISTA started it from a task result or at startup, or
  another view did) is drawn in the open conversation: when that conversation's run status
  changes while nothing is watched, the page attaches if a run is going and replays the
  stored events if it ended between polls. The tab asks for the status at once when a task
  ends. A view following a run it watched to its end follows `next_run_id` instead.
- The event row is a new renderer in the run renderer for `inbox_taken` items of kind
  `task_event`.
- The delete dialog reads the conversation's live tasks when it opens, names them, and confirms
  ending them; a task that started since is refused by the endpoint and shown instead.

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
- [A campaign tool's flushed, uncommitted writes hold SQLite's write lock for the rest of its
  turn, and the inbox take writes through its own session] → the take reads first, which
  needs no write lock, and a locked write skips that step; delivery waits for the next step or
  for the turn's end. Campaign mode is off by default.

## Migration Plan

Two new tables and two columns with server defaults; `db/schema_check.py` passes on existing
installs. No data is rewritten. Rolling back to a build without the change leaves the new
tables and columns unused.

The UI and backend ship together in one package, so the changed send response has no
out-of-step client.
