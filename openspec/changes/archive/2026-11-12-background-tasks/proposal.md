## Why

Every sandbox command holds the agent's turn until it exits, and the only way the agent can
follow a cluster job is to poll it inside a turn. A researcher who starts a twenty-minute fit
or submits a Frontier job either keeps the conversation locked or has to come back and ask.
The agent should be able to start work, let the conversation go, and be told when the work
ends.

## What Changes

- **Background commands.** `run_bash` takes `background` and `timeout`. A background run
  returns a task id at once; its output goes to a log file on the project volume; its exit
  is reported to the conversation.
- **Job watches.** A new agent tool, `watch_hpc_job(job_id, cluster)`, has the backend follow
  a submitted job on any cluster and report when it starts running and when it ends. Watches
  are saved and resume when VISTA is reopened.
- **One inbox per conversation.** Researcher messages, command exits and job changes all
  enter one inbox. With a turn running, an item is handed to the agent at its next step; with
  the conversation idle, VISTA starts a turn itself.
- **Messages queue while the agent is busy.** The message box is no longer disabled during a
  turn. A queued message can be edited or deleted until the agent takes it.
  **BREAKING** for the chat API: a send to a busy conversation is queued, not refused with a
  conflict.
- **Guards on self-started turns.** At most 10 in a row without a message from the
  researcher. Stop makes a conversation quiet until the researcher writes again. Expired
  cluster credentials pause a watch and ask the researcher, with no agent turn.
- **Background tab.** A tab in the workspace column lists the open conversation's tasks, each
  with state, runtime, a link to its log and a stop control. A self-started turn opens with a
  one-line event row in place of a researcher message.
- **Limits and cleanup.** 4 background commands and 8 job watches per conversation; a command
  runs 1 hour by default and 24 hours at most; a log stops growing at 10 MB; logs and
  finished tasks are kept until the conversation is deleted, and deleting a conversation with
  running tasks asks first.
- **Agent tools** `list_tasks` and `stop_task`, answered from the backend's records so they
  work after the agent is rebuilt by a Settings or model change.

Not in this change: timed wake-ups, monitors that wake the agent on each output line, email
when a watched job ends, and any change to campaign mode's monitor.

## Capabilities

### New Capabilities

- `background-tasks`: background sandbox commands and HPC job watches: how they start, what
  they report, their limits, how they are listed and stopped, and what survives a restart, an
  agent rebuild or a deleted conversation.
- `conversation-inbox`: the per-conversation inbox that delivers researcher messages and task
  results to the agent: queueing during a turn, self-started turns, the cap on them, and what
  Stop does to what is waiting.

### Modified Capabilities

- `chat-runs`: a message sent to a conversation with an active run is queued instead of
  refused, and the message box stays enabled.
- `ui-agent-activity`: the workspace column gains a Background tab.
- `code-execution-sandbox`: a command can outlive the call that started it, and stopping it
  ends everything it started.

## Impact

- **dev MCP server** (`mcp_servers/dev_mcp_server`): `run_bash` gains a background path;
  two tools the agent never sees (wait for a task, signal a task).
- **Backend** (`backend/src/vista_backend`): new inbox and task services and their tables
  (additive, no change to existing columns); `services/chat_run.py` starts turns from the
  inbox and records each turn's origin; `agents/agents.py` gains the three agent tools, a
  per-run hook that hands inbox items to the running agent, and interception of background
  `run_bash` calls; new API routes for the inbox and for tasks; startup resumes watches and
  marks lost commands.
- **vista MCP server**: `get_hpc_job_status` failures for expired credentials and "too many
  requests" become recognisable to the watcher. No change to what the agent sees.
- **UI** (`ui/`): message box and queued-message chips, Background tab, event row, delete
  dialog.
- **Specs**: two new, three modified (above). `docs/` and `AGENTS.md` gain a section.
- **No new dependencies.** PydanticAI's `RunContext.enqueue` (present in the pinned 1.105.0)
  carries mid-turn delivery.
