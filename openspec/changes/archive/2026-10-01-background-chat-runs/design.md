## Context

Today a chat turn is the body of the SSE generator in `POST /projects/{p}/agent/run`
(`api/agent.py`). `ProjectAgent.run_stream` puts the PydanticAI loop in a `StreamMerger` task,
and the merger cancels that task when its consumer closes (`utils/streams.py`), so a client
disconnect cancels the turn. History is appended only on the final `ProjectAgentResultEvent`.
The page (`ui/app/page.tsx`) builds the visible transcript (`messages`) from the stream and
saves it, *and* writes `message_history`, so two writers share the row.

Desktop constraints that shape the design (see proposal.md for motivation):

- One backend process, one user, one VISTA window (plus any browser tab on the same local
  port). The agent pool (`services/project_agent.py`) is already process-local.
- Quitting VISTA sends TERM with a 10 s grace on macOS/Linux; the Windows launcher's Job Object
  kills services with no grace.
- Debates already run detached (`api/debate.py`: `_spawn`, a stream session factory, replay on
  reconnect). This design follows that shape.
- `init_db` adds missing columns to existing tables at startup, so new `chat_session` columns
  need no migration.

## Goals / Non-Goals

**Goals:**
- A run registry that owns turns, with HTTP streams as subscribers.
- One writer of `message_history`: the backend.
- Replay that draws an unwatched turn with the page's existing event dispatcher.

**Non-Goals:**
- Durable resume of a turn across restarts; checkpointing mid-turn to disk.
- A push channel for status: the nav rail and list poll.
- Changing `StreamMerger` or the PydanticAI event shapes the page already parses.

## Decisions

### D1. An in-process run registry keyed by conversation

`services/chat_run.py` holds `ChatRun` objects keyed by `chat_session_id`. Each run has:
- the task, held strongly as in `api/debate.py`'s `_spawn`;
- an append-only list of `(seq, event)`;
- a status;
- the pending prompts;
- an `asyncio.Condition` that wakes subscribers.

The task does three things:
1. It enters `project_agent_pool.get(key)` and holds that lease for the whole turn, so TTL, LRU
   and settings-change evictions wait for it.
2. It opens its own `AsyncSession` through a module-level factory, the same pattern as debates,
   so tests can override it. It passes that session to `run_stream(db_session=...)` for
   campaign mode.
3. It consumes the merger itself.

A subscriber is an async generator over the event list: it yields everything from `after`,
waits on the condition, and returns when the run ends. Closing a subscriber touches nothing
else.

*Alternative:* a DB-backed event log that subscribers poll, as debates do. That would survive
multiple workers, which desktop doesn't need, and it costs a write per text delta. Rejected.

### D2. Run state lives on `chat_session`

New columns:
- `run_state`: `idle | running | done | failed | interrupted | stopped`;
- `run_unseen`: boolean;
- `run_events`: nullable JSON.

The row is written three times: `running` when the turn starts; the final state, history and
compacted events when it ends; and cleared when seen. "Needs you" is never stored: it comes from
the live registry, since no prompt can survive a restart. At startup, `running` rows become
`interrupted` with `run_unseen = true`.

*Alternative:* a separate `chat_run` table. It adds nothing while a conversation has at most one
active run, and it would need joins for the list view. Rejected.

### D3. Partial history on Stop, interruption and failure

`agent_stream` wraps `run_stream_events` in PydanticAI's `capture_run_messages()`. On
cancellation or error, the run's messages beyond the prior history are trimmed to the last
complete step:
- A trailing `ModelResponse` whose tool calls have no matching returns is dropped.
- A request holding tool returns is kept.

A short `ModelResponse` text part is then appended ("Stopped by the researcher." or "Interrupted
when VISTA quit."), so the history alternates cleanly before the next prompt. That trimmed list
is what gets saved.

*Alternative:* switch to `agent.iter()` and read `run.all_messages()`. It's equivalent, but a
larger edit to a loop that PALISADE and logging hook into. A test must confirm
`capture_run_messages` sees messages when the run task is cancelled mid-tool; if it doesn't,
fall back to `iter()`.

### D4. Shutdown and interruption

The lifespan calls `chat_run_registry.stop_all(reason="interrupted")` before
`project_agent_pool.clear()`, waiting at most about 5 s so it fits inside the launcher's 10 s
grace. Each run writes its partial history the D3 way.

If the process dies without that, as with the Windows Job Object or a KILL, the D2 startup sweep
marks the turn interrupted. Only what the page had already saved survives: the prompt bubble and
the steps it had drawn. The spec's "as far as it was saved" covers exactly this.

### D5. Prompts: no timeout, resolution is an event

Remove the two `asyncio.wait_for(..., timeout=5*60)` calls in `agents/agents.py`. A prompt now
waits on its future, and Stop cancels the task, which cancels the wait.

Raise `get_vista_mcp_server(read_timeout=...)` from `1800+60` to 24 h. `dev_mcp_server` is
unchanged.

The registry records each prompt event as pending. `POST /projects/{p}/elicitation` resolves it
and then appends a `prompt_resolved {elicitation_id}` event, which clears the prompt in every
other view. Replay skips prompt events that are already resolved, along with their
`prompt_resolved` events. Answers only ever pass through the resolve call, so they never reach
the event list.

### D6. API

| Route | Behaviour |
|---|---|
| `POST /projects/{p}/agent/run` (`stream: true`) | Requires `chat_session_id`. Returns 409 if that conversation has an active run. Otherwise starts the run and streams it as a subscriber from seq 0. |
| `POST …/agent/run` (`stream: false`) | Same registry: starts the run and awaits its result, so the 409 rule holds for API clients too. |
| `GET /projects/{p}/chat-sessions/{id}/run/events?after=N` | SSE replay then tail, with SSE `id:` set to seq. Returns 204 when no run is active. |
| `POST /projects/{p}/chat-sessions/{id}/run/stop` | 202, or 404 when no run is active. |
| `GET /projects/{p}/chat-runs/status` | `[{chat_session_id, status, unseen}]` for the project, with `status` in `working / needs_you / done / failed / interrupted / stopped / idle`. |
| `GET /projects/{p}/chat-session` | Also returns `run_status`, `run_unseen` and `run_events`. |
| `PUT /projects/{p}/chat-session` | Ignores `message_history` (**BREAKING**). A new `ack_run: true` clears `run_unseen` and `run_events`. |

Each run's first event is `run_started {run_id, user_prompt}` and its last is
`run_finished {state}`, so replay can draw the question and the ending without page-side
knowledge. The Next proxy gains matching routes under `ui/app/api/chat/`.

*Alternative for status:* a project-wide SSE. Polling a tiny local JSON every 3 s is simpler on
desktop and stops when the page is hidden. Rejected for now.

### D7. Stored events are compacted

On persist, each part's `PartStartEvent` + deltas + `PartEndEvent` becomes one `PartStartEvent`
carrying the final part, followed by its `PartEndEvent`. That's the shape campaign progress
already emits, which the page draws. Prompt events and `prompt_resolved` are dropped. Log and
tool events are kept. The live in-memory list keeps raw events: it's only held for one run.

### D8. The page: one renderer, runs tagged, abort is safe

- The dispatcher moves out of the send closure into a function that applies an event to a
  given conversation's state.
- `ChatTranscriptMessage` gains an optional `run_id`. Bubbles drawn from a run carry it.
- Replaying a run first removes that run's bubbles, then dispatches. Redrawing after a partial
  save therefore never duplicates.
- The send fetch and the re-attach stream get an `AbortController`, aborted on conversation
  switch and unmount. That's now harmless.
- When a conversation is opened, the page:
  - re-attaches from 0 if a run is active;
  - otherwise, if `run_events` is present, replays them, saves with `ack_run`, and clears the
    dot;
  - otherwise, if `run_unseen` is set, saves with `ack_run` to clear the dot.
- Input is disabled while a run is active; Stop calls the stop route.
- The conversation list and `NavRail` share one status hook that polls D6's status route.

## Risks / Trade-offs

- [A tool that really hangs holds the run and its lease for up to 24 h] → Stop ends it. The
  working dot keeps the stuck run visible.
- [A settings change (new API key) waits for a long-waiting run before the agent is rebuilt] →
  That's already true of any leased agent, and Stop releases it. Worth one line in the settings
  help text only if it confuses anyone.
- [`capture_run_messages` may not see messages across the task boundary on cancel] → Cover it
  with a test first (tasks 1.x). Fall back to `agent.iter()` (D3).
- [Windows loses unsaved progress on quit (no grace period)] → Accepted: the startup sweep
  still marks the turn interrupted.
- [Raw event lists grow with long streamed answers while a run is live] → Held for one run in
  one process, then compacted on persist.
- [Two views answering the same prompt at once] → The first resolve wins. The second gets
  today's "not found" and clears on `prompt_resolved`.

## Migration Plan

None. New columns are added at startup by `init_db`. The UI and backend ship in one MR.
`PUT /chat-session`'s `message_history` is silently ignored, not rejected, so a stale page open
during an upgrade doesn't error.

## Open Questions

- The exact look of the "needs you" ring and the dot animation. They're styling, settled during
  implementation against the existing nav rail tokens.
