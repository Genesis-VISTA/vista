## Why

A chat turn lives inside the HTTP response that streams it. On desktop, leaving the chat page
orphans the run: its answer never reaches the transcript, approval and SSH-login prompts expire
after 5 minutes, and a message sent meanwhile overwrites the unseen turn. Closing the tab
(Windows' browser mode) cancels it outright. Long HPC work is exactly what researchers walk
away from.

## What Changes

- The backend owns each chat turn as a background task; HTTP streams are watchers that can
  re-attach (replaying from the start). Disconnecting stops watching; only Stop cancels.
- One active run per conversation; several conversations may run at once. Sending into a busy
  conversation is refused, and the UI disables the input and shows Stop instead.
- Stop keeps what happened: the turn stays in the transcript marked "Stopped", and model
  history keeps every completed tool call, so the agent remembers a job it submitted. Quitting
  mid-turn is recorded the same way and shows as interrupted on relaunch.
- Prompts (tool approvals, form and URL elicitations, including the Lux SSH login) wait until
  answered or stopped instead of timing out at 5 minutes. The per-call timeout on the VISTA MCP
  server rises from ~31 minutes to 24 hours so an SSH login raised inside a tool call can wait
  too.
- Status dots in the conversation list: amber glowing (working), amber with a ring (needs you),
  green (done, unseen), red (failed or interrupted, unseen). The nav rail's Chat entry carries one
  summary dot; clicking it opens the single conversation that needs you, if there is exactly one.
  The unseen flag is stored in the database and survives a restart.
- A turn nobody watched is drawn by replaying its stored events through the live renderer;
  the events are deleted once the transcript is saved.
- The UI stops writing model history; only the backend does. **BREAKING** for the
  `PUT /chat-session` payload (its `message_history` field is ignored). No deployments depend
  on it.

## Capabilities

### New Capabilities
- `chat-runs`: a chat turn's lifetime independent of any page watching it, its prompts, and
  the status shown in the conversation list and nav rail.

### Modified Capabilities
(none — no existing spec covers the chat turn)

## Impact

- Backend: a new run registry, the agent and chat-session APIs, prompt timeouts, three new
  `chat_session` columns (added at startup, no migration). Files are listed in design.md.
- UI: the chat page, the nav rail, new proxy routes under `app/api/chat/`.

## Non-goals

- Keeping runs alive after VISTA quits, a quit warning, or any change to the Electron shell.
- Resuming an interrupted turn after a restart (it is shown as interrupted; the researcher
  resends).
- OS notifications or email when a run needs attention.
- Multi-worker or multi-user hosting: the run registry is in-process, like the agent pool.
- VISTAGuard (PALISADE gates) behaviour, beyond approvals no longer timing out.
