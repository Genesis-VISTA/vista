# Design

## Context

- Every message to the agent goes through the conversation's inbox (`services/inbox.py`,
  background-tasks). A turn starts only through `ChatRunRegistry.wake`, which takes everything
  queued and applies the hold-back rule; a running turn takes new items between its steps
  (`InboxDelivery`). `POST /projects/{p}/agent/run` puts the message in the inbox and wakes the
  conversation.
- VISTA also starts turns by itself: when a background command ends or a job watch
  (`watch_hpc_job`, polled through the hidden `read_hpc_job_state`) reports a job's end, the
  result goes into the inbox and wakes the conversation. At most 10 such turns run in a row.
- A run's events are buffered on a `ChatRun` that any number of watchers can `subscribe()` to.
  Prompts (`mcp_tool_approval`, `mcp_form_elicitation`, `mcp_url_elicitation`) are answered with
  `chat_run.resolve_prompt`, first answer wins. A form whose only field is a choice among fixed
  answers (a file transfer's Approve/Decline) is shown in the app as buttons.
- Tool approval is PydanticAI deferred tools resolved by `PalisadeApprovalCapability`, whose
  callback is `ProjectAgent._request_tool_approval`; it waits with no timeout. PALISADE passes the
  call's arguments as the model sent them, which for OpenAI-compatible providers is a JSON string.
- The vista MCP server has `submit_hpc_job`, `cancel_hpc_job`, `hpc_upload_files`,
  `hpc_download_files` (not read-only) and `get_hpc_job_status`, `get_hpc_job_outputs`
  (read-only); `agenthpc` adds its own submit and cancel tools. Lux logs in over SSH through a
  form elicitation; the login is cached per researcher for an hour.
- The only known credential expiry is S3M's `plannedExpiration` (`services/hpc_status.py`).
- Database secrets are Fernet-encrypted with `VISTA_BACKEND_ENCRYPTION_KEY`.
- Schema changes are additive: `init_db` runs `create_all` and adds missing columns.

## Goals / Non-Goals

**Goals:**
- One agent core and one inbox for app and Slack; Slack-specific behaviour only through the
  run's channel.
- Every Slack safeguard enforced in code VISTA controls, not by the model's cooperation.
- Everything testable against a fake Slack transport and fake MCP results.
- Channel-neutral seams on the agent side, so a second chat platform needs an adapter, not a fork.

**Non-Goals:**
- A hosted relay or router, OAuth multi-workspace install, slash commands, Teams or Discord,
  voice via Slack.
- Slack's agent messaging experience (`agent_view`, `agents.sessions.*`): a later spike.
- Running Slack from a laptop with the window closed: background mode, sleep handling, offline
  notices and the single-instance lease are a follow-up (see "Not in this change").
- Shared deployments: one Slack app is served by one researcher's VISTA.
- Queuing messages sent while VISTA is offline (Socket Mode cannot).

## Decisions

### D1. A conversation's channel, through the inbox
`chat_session.origin` (`app` | `slack`) is set when the conversation is created and never
changes. `ChatRunRegistry.wake` reads it and passes it as the run's `channel` to
`ProjectAgent.run_stream(channel=...)`, stored for the run like the run's approval emitter, so
every turn in a Slack conversation is a Slack turn: those started by a Slack message and those
VISTA starts itself. Capabilities read it through a lambda, so one pooled agent serves both
channels. The Slack handler puts each message in the inbox (`inbox.put`, kind `message`) and
wakes the conversation; there is no second way to start a turn. The backend refuses a desktop
send or prompt answer in a Slack conversation (`409`), so read-only is enforced on the server,
not only in the UI; Stop is allowed. *Alternative:* the channel per inbox item — rejected, a
turn takes several items and the desktop would then write into a Slack thread.

### D2. Slack code behind a transport
`slack/transport.py` defines `SlackTransport` (start, stop, an inbound handler, and post /
update / ephemeral / upload / publish_home / react / history calls). `slack/socket_mode.py` is
the only implementation: Bolt's `AsyncApp` with `AsyncSocketModeHandler`, using an
`AsyncWebClient` with `AsyncRateLimitErrorRetryHandler`. Handlers depend on the interface; tests
use `FakeTransport`. Bolt's default middleware (self-event filtering, request verification) is
kept. `connect` records the socket only once it is open; a failed connect leaves the service
disconnected with the reason and is retried. *Alternative:* raw `slack_sdk` Socket Mode
client — Bolt gives acking and action routing.

### D3. Access is a middleware before any handler
Order: workspace allowlist (drop silently) → channel allowlist (DMs exempt, for messages and
button taps alike) → `lock` / `link` keywords → link lookup (refuse, rate-limited per user). Link
codes are 6 digits, random, stored hashed with a 10-minute expiry, created only from a DM;
confirming one needs the desktop API.

### D4. Confirmation as a capability, not a prompt to the model
`SlackGateCapability.wrap_tool_execute`, active on a Slack turn: a tool whose
`tool_def.metadata.annotations.readOnlyHint` is not `True` is first checked against the lock,
the limits and credential expiry (a refusal returns a tool error text instead of asking). Tools
in the confirmed set — job submission (`submit_hpc_job`, `agenthpc_submit_parameter_set`) and
cancellation (`cancel_hpc_job` and the `agenthpc` cancels) — then raise `ApprovalRequired`; every
other tool runs, so sandbox work does not stop at each step. Limits are checked against the
values `preview_hpc_job` resolves (the job's defaults included), not the model's arguments. The
existing approval path emits `mcp_tool_approval`, with the arguments parsed into a dict when they
arrive as a string; the Slack relay renders it as Block Kit with fields from `preview_hpc_job`,
called through `direct_call_tool` with the user's metadata. Button values carry only the
elicitation id; Confirm requires the Confirm button's action id, checks the clicker against the
conversation's Slack user, and rechecks lock and limits before `resolve_prompt`. In a turn VISTA
started, the card @mentions that user. Pending confirmations are also in `slack_pending` so an
expired or already-answered card can be redrawn after a restart. Native tools that only read —
`list_my_recent_jobs`, `watch_hpc_job`, `list_tasks` — are tagged
`metadata={"annotations": {"readOnlyHint": True}}`.

### D5. Prompts in Slack runs
`_make_mcp_elicitation_callback` on a Slack turn: a form whose only field is a choice among fixed
answers (a transfer's Approve/Decline, `offer_choices`) is emitted as usual and the Slack relay
shows it as buttons, requester-only, declined after 10 minutes; any other form (a login) or URL
elicitation is declined at once, and the Slack instructions tell the agent that logins happen in
the desktop app. A Lux submission thus works from Slack exactly while the desktop's cached login
lasts (an hour).

### D6. Threads, origin and transcript
`slack_thread(team_id, channel_id, thread_ts)` → `chat_session_id`, requester and project: the
link's chosen project if one is set in Settings → Slack, otherwise the one the user picks. With
only one project VISTA uses it without asking. Otherwise the first message of a new thread is
answered in the thread with "Which project should this go in?" and a button per project (a menu
past five); only the requester's tap counts, and the choice holds for that thread only. The
message, and any replies sent before the tap, are held in `slack_project_ask(team_id,
channel_id, thread_ts, slack_user_id, prompts, created_at)` (a new table, so the change stays
additive) and go into the new conversation's inbox in order once a project is tapped. A held
ask survives a restart and does not expire. The conversation is created with `origin="slack"`,
which drives a badge and the read-only view in the UI. A message in a thread whose turn is
running waits in the inbox, with no cap; VISTA reacts to it with ⏳ (`hourglass_flowing_sand`)
and swaps that for ✅ when a turn takes it (`reactions:write`). The relay follows every run of a
Slack conversation, including those VISTA starts, so nothing in it happens out of the thread's
sight. After each turn the relay appends user/assistant bubbles to `chat_session.messages` so
the desktop shows the conversation.

### D7. Relay: one progress message, edited
`RunRelay` subscribes to the `ChatRun`, posts "Working on it…", edits it at most every 1.5 s with
the current step (tool name mapped to a phrase), and on `run_finished` replaces it with the
formatted answer. `chat.update` failures fall back to posting. A card that cannot be posted
declines its prompt, and a turn that fails in any way replaces the progress message with what
happened. Every Slack call goes through the SDK's retry handler for 429s.

### D8. Formatting
`slack/format.py`: Markdown → mrkdwn (bold, italics, links, headings, lists, code, tables as code
blocks), with `&`, `<`, `>` escaped everywhere, code included. The answer is split into summary
(first paragraph, trimmed to ~300 characters at a sentence boundary) and detail. Detail is cached
(`slack_detail`, keyed by a random id, 24 h). "Show more" expands the answer in place: the tap
edits the answer into the full text and removes the button, so a second tap is impossible and
nothing is posted twice; the notification still shows only the summary. What does not fit in one
message stays in place up to the limit, and the rest is posted once below it in chunks under
Slack's limits (section text 3,000, message text 40,000, 50 blocks; we use 2,900 /
3,800-per-chunk / 40). *Alternative:* posting the detail as new messages on every tap, as !163
did — a repeated tap repeated the post.
Chunking terminates on any input, an unclosed code fence included, and stray NULs are stripped
before placeholders are used. Any `msg_too_long` or `invalid_blocks` response falls back to a
plain truncated text post; the handler never raises on length.

### D9. Job record and watches
`hpc_job_record`: user, chat_session, origin, Slack thread ref, cluster, job_id, job, nodes,
duration, args, submitted_at. Written in `chat_run` on a successful `submit_hpc_job` result
(parsed by the existing `parse_submit_summary`) for every channel; it serves
`list_my_recent_jobs(cluster=None, limit=10)` (read-only) and the daily limit. Campaign
`HpcJobTable` is untouched. A successful submission in a Slack conversation also registers a job
watch for it (`services/background_tasks`, as `watch_hpc_job` would), so the watch's report of
the job's end starts a turn in that conversation and the relay posts it in the thread. A watch
outlives a restart, so a job that ended while VISTA was off is reported when it is back. There is
no separate Slack job monitor and no setting for desktop jobs: a desktop conversation never posts
to Slack.

### D10. Ambiguity buttons
Native tool `offer_choices(question, options)` (≤5, read-only, only registered on the Slack
channel's toolset filter) raises a form elicitation with one choice field that the Slack relay
renders as buttons and resolves with the tapped option; requester-only and 10-minute expiry as in
D4. On the app channel it is filtered out.

### D11. Queue and preview tools
`get_hpc_queue_status(cluster)`: Lux runs `squeue -h -o '%T|%D'` over the cached connection and
summarises; OLCF/NERSC return IRI facility status and incidents plus the researcher's own jobs if
the IRI client can list them, and state that queue-wide depth is not exposed. `preview_hpc_job`
shares submission's argument validation and account resolution without network calls.

### D12. Removed: single instance by a lease in Slack
Moved to the laptop follow-up. Without it, two machines connected with the same app token each
receive a share of the events; `docs/slack.md` says to turn Slack on in one place only.

### D13. Removed: notices, sleep warnings and sleep prevention
Moved to the laptop follow-up.

### D14. Credential expiry
`slack/notices.CredentialReminder` every 15 min per linked user: S3M from
`HpcStatusService`'s `expires_at`; NERSC from the token's JWT `exp` when it is one; Globus HA as
connect time + 3 days, labelled estimated (needs a `globus_connected_at` stamp per cluster).
Reminders at ≤24 h and ≤2 h, each recorded in `slack_reminder(user, cluster, kind, expires_at)`
so a renewed credential gets fresh reminders. Expired clusters are listed in the Slack prompt
addendum and refused by the gate.

### D15. App Home
Published on `app_home_opened`, on lock/link/job change, and every 5 minutes, with an "as of"
time so a stale view is obvious (Slack keeps the last published view when we are offline).

### D16. Secrets
`utils/secret_store.py` keeps the Slack tokens (`slack-bot-token`, `slack-app-token`): in
`keyring` (service `VISTA`, entries scoped to the data directory) on a desktop, refusing
`keyring.backends.fail` / `null` and plaintext backends; in a mode-0700 directory when
`settings.deployment` is `hosted`. Without either, Slack refuses to enable. The database key is
unchanged; moving it into the secret store is in the laptop follow-up.

### D17. Removed: background mode
Moved to the laptop follow-up.

### D18. Audit
`slack_audit` rows and `logs/slack-audit.jsonl` lines: time, team, Slack user, VISTA user,
thread, event (request / refused / confirm_posted / confirmed / declined / expired /
tool_result), tool, redacted args, job id. Args pass through a redactor that drops keys
matching token/password/secret/passcode.

### D19. Restarts leave nothing waiting
Saving Slack settings restarts the service. `stop()` declines every open card's prompt before it
cancels the expiry timers, and at start-up `close_stale` declines as well as redraws, so no run
waits on a card nobody can answer.

### D20. Channel-neutral seams
The agent side knows only that a run's channel is not `app`: the gate, the prompt rules and the
instructions switch on that, and `channel` is a plain string. `slack/` stays one package: access,
linking, threads, confirmation, limits and audit are not extracted until a second adapter exists.
The only mature multi-platform adapter, Vercel's Chat SDK (TypeScript), would run beside the
backend as a sidecar if one is needed.

## Risks / Trade-offs

- [The model ignores summary-first instructions] → the formatter enforces the summary length.
- [Model calls a destructive tool without annotations] → anything not marked read-only is checked
  against the lock; cluster actions are named explicitly in the confirmed set.
- [Two machines with the same tokens] → no lease in this change; documented, and the pilot runs
  one instance.
- [A turn VISTA starts needs a confirmation nobody is waiting for] → the card @mentions the
  conversation's owner and expires after 10 minutes, so it never runs unattended.
- [Crash or network drop] → Slack shows the app as away; a message sent meanwhile is lost
  (Socket Mode), documented in `docs/slack.md`.
- [Keyring prompts on macOS for an unsigned binary] → the backend's Python is the requester;
  first access may prompt once; documented.

## Migration Plan

Additive tables and columns via `init_db`. Slack is off until configured.
