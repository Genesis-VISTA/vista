# Design

## Context

- A turn today is started by `POST /projects/{p}/agent/run` (`api/agent.py`), which resolves the
  pooled `ProjectAgent`, loads `chat_session.message_history` and calls
  `chat_run_registry.start()`. The run is driven in a task and its events are buffered on a
  `ChatRun` that any number of watchers can `subscribe()` to. Prompts (`mcp_tool_approval`,
  `mcp_form_elicitation`, `mcp_url_elicitation`) are answered with `chat_run.resolve_prompt`,
  first answer wins.
- Tool approval is PydanticAI deferred tools resolved by `PalisadeApprovalCapability`, whose
  callback is `ProjectAgent._request_tool_approval`; it waits with no timeout.
- The vista MCP server has `submit_hpc_job`, `cancel_hpc_job` (not read-only) and
  `get_hpc_job_status`, `get_hpc_job_outputs` (read-only). Lux logs in over SSH through a form
  elicitation; the login is cached per researcher for an hour.
- Only campaign jobs are recorded (`HpcJobTable`, tied to a campaign step). `CampaignMonitor`
  polls them, off by default.
- The only known credential expiry is S3M's `plannedExpiration` (`services/hpc_status.py`).
- Electron owns the services' lifetime; the launcher has a foreground headless mode
  (`VISTA_NO_WINDOW=1`) used only by the smoke test. Nothing registers a login item.
- Database secrets are Fernet-encrypted with `VISTA_BACKEND_ENCRYPTION_KEY`, which packaged
  installs leave at the development default.
- Schema changes are additive: `init_db` runs `create_all` and adds missing columns.

## Goals / Non-Goals

**Goals:**
- One agent core for app and Slack; Slack-specific behaviour only through a per-run channel.
- Every Slack safeguard enforced in code VISTA controls, not by the model's cooperation.
- Everything testable against a fake Slack transport and fake MCP results.

**Non-Goals:**
- A hosted relay, OAuth multi-workspace install, slash commands, Teams, voice via Slack
  (relay is a design note in `docs/slack-relay.md`).
- Shared/server deployments: one Slack app is served by one researcher's machine.
- Queuing messages sent while VISTA is offline (Socket Mode cannot).

## Decisions

### D1. A channel per run, one shared entry point
`services/conversation.start_turn(user, project, chat_session_id, text, channel, origin_ref)`
does what `api/agent.py` does today and returns the `ChatRun`; the endpoint calls it with
`channel="app"`. `ChatRun.channel` reaches `ProjectAgent.run_stream(channel=...)`, stored for
the run like the run's approval emitter. Capabilities read it through a lambda, so one pooled
agent serves both channels. *Alternative:* a separate Slack agent — rejected, it would fork tools,
prompts and pooling.

### D2. Slack code behind a transport
`slack/transport.py` defines `SlackTransport` (start, stop, an inbound handler, and post /
update / ephemeral / upload / publish_home / pin / history calls). `slack/socket_mode.py` is the
only implementation: Bolt's `AsyncApp` with `AsyncSocketModeHandler`, using an
`AsyncWebClient` with `AsyncRateLimitErrorRetryHandler`. Handlers depend on the interface; tests
use `FakeTransport`. Bolt's default middleware (self-event filtering, request verification) is
kept. *Alternative:* raw `slack_sdk` Socket Mode client — Bolt is what the requirements ask for
and gives acking and action routing.

### D3. Access is a middleware before any handler
Order: workspace allowlist (drop silently) → channel allowlist (DMs exempt) → `lock` / `link`
keywords → link lookup (refuse, rate-limited per user). Link codes are 6 digits, random, stored
hashed with a 10-minute expiry, created only from a DM; confirming one needs the desktop API.

### D4. Confirmation as a capability, not a prompt to the model
`SlackGateCapability.wrap_tool_execute`, active when `channel == "slack"`: a tool whose
`tool_def.metadata.annotations.readOnlyHint` is not `True` raises `ApprovalRequired`, after
the gate has checked lock, limits and credential expiry (a refusal returns a tool error text
instead of asking). The existing approval path then emits `mcp_tool_approval`; the Slack relay
renders it as Block Kit and starts a 10-minute timer that resolves `decline`. For
`submit_hpc_job` the card's fields come from `preview_hpc_job`, called by the gate through
`direct_call_tool` with the user's metadata, so the card shows what would actually be
submitted, not the model's description. Button values carry only the elicitation id; the
handler checks the clicker against the requester and rechecks lock and limits before
`resolve_prompt`. Pending confirmations are also in `slack_pending` so an expired or
already-answered card can be redrawn after a restart. Native tools that only read are tagged
`metadata={"annotations": {"readOnlyHint": True}}`.

### D5. Forms are declined in Slack runs
`_make_mcp_elicitation_callback` returns `decline` at once when the channel is Slack, and the
Slack system-prompt addendum tells the agent that logins happen in the desktop app. A Lux
submission thus works from Slack exactly while the desktop's cached login lasts (an hour).

### D6. Threads, origin and transcript
`slack_thread(team_id, channel_id, thread_ts)` → `chat_session_id`, requester and project (the
link's chosen project, defaulting to the user's first project). `chat_session.origin`
(`app`|`slack`) drives a badge in the UI. After each turn the relay appends user/assistant
bubbles to `chat_session.messages` so the desktop shows the conversation. A message arriving
while that thread's run is busy is queued per thread (in memory, at most 3), then run.

### D7. Relay: one progress message, edited
`RunRelay` subscribes to the `ChatRun`, posts "Working on it…", edits it at most every 1.5 s
with the current step (tool name mapped to a phrase), and on `run_finished` replaces it with the
formatted answer. `chat.update` failures fall back to posting. Every Slack call goes through the
SDK's retry handler for 429s.

### D8. Formatting
`slack/format.py`: Markdown → mrkdwn (bold, italics, links, headings, lists, code, tables as
code blocks). The answer is split into summary (first paragraph, trimmed to ~300 characters at a
sentence boundary) and detail. Detail is cached (`slack_detail`, keyed by a random id, 24 h) and
posted by "Show more" in chunks below Slack's limits (section text 3,000, message text 40,000,
50 blocks; we use 2,900 / 3,800-per-chunk / 40). Any `msg_too_long` or `invalid_blocks` response
falls back to a plain truncated text post; the handler never raises on length.

### D9. Job record and monitor
`hpc_job_record`: user, chat_session, origin, Slack thread ref, cluster, job_id, job, nodes,
duration, args, submitted_at, state, finished_at, notified_at. Written in `chat_run` on a
successful `submit_hpc_job` result (parsed by the existing `parse_submit_summary`) for every
channel; campaign `HpcJobTable` is untouched. `slack/jobs.JobMonitor` (same loop shape as
`CampaignMonitor`) runs only with Slack enabled, every 120 s, over records with no terminal
state that need notifying (Slack origin, or any origin when the desktop-jobs setting is on),
calling `get_hpc_job_status` through `direct_call_tool` with the user's metadata. Terminal state
→ notify → `notified_at`. Since it reads the DB, jobs that ended while VISTA was off are found on
the first pass. Lux without a cached login is skipped and shown as "needs desktop login" on Home.
Native tool `list_my_recent_jobs(cluster=None, limit=10)` (read-only) serves references.

### D10. Ambiguity buttons
Native tool `offer_choices(question, options)` (≤5, read-only, only registered on the Slack
channel's toolset filter) raises a form elicitation with `mode="choice"` that the Slack relay
renders as buttons and resolves with the tapped option; requester-only and 10-minute expiry as in
D4. On the app channel it is filtered out.

### D11. Queue and preview tools
`get_hpc_queue_status(cluster)`: Lux runs `squeue -h -o '%T|%D'` over the cached connection and
summarises; OLCF/NERSC return IRI facility status and incidents plus the researcher's own jobs if
the IRI client can list them, and state that queue-wide depth is not exposed. `preview_hpc_job`
shares submission's argument validation and account resolution without network calls.

### D12. Single instance by a lease in Slack
Two machines with the same app token would both receive a share of events, so the lease must
be visible to both: a pinned "VISTA status" message in the bot's DM with the first linked user,
whose message `metadata` carries `{instance_id, host, heartbeat_at}`. Before connecting, and
every 60 s while connected, the instance reads it (`pins.list`/`conversations.history` via the
Web API, which works without a socket). Another instance's heartbeat younger than 180 s → stay
disconnected and report it. Take over writes our id; the old instance sees a foreign id at its
next heartbeat and disconnects. Edits send no notification. *Alternative:* a local lockfile —
cannot see other machines.

### D13. Notices and debounce
`slack_config` keeps `offline_notice_sent_at` and `last_online_notice_at`. Shutdown (lifespan
`finally`, 3 s budget) and pre-sleep send the offline notice and set the flag; on connect,
back-online is sent only if the flag is set and the last notice is older than 2 minutes, then
the flag is cleared. Sleep warnings: macOS IOKit `IORegisterForSystemPower` on a thread via
ctypes; Linux logind `PrepareForSleep` with a delay inhibitor (`dbus-fast`); Windows
`PowerRegisterSuspendResumeNotification`. Each is optional — failure to register only costs the
sleep notice. Sleep prevention: `caffeinate -i -w <pid>`, `systemd-inhibit --what=idle:sleep`,
`SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)`.

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
`utils/secret_store.py` wraps `keyring` (service `VISTA`, entries scoped to the data directory, names `slack-bot-token`,
`slack-app-token`, `db-encryption-key`). On a backend without a real keyring
(`keyring.backends.fail` / `null`), Slack refuses to enable. The packaged launcher asks the
backend for the encryption key at start: if `VISTA_BACKEND_ENCRYPTION_KEY` is unset and the
key is not in the keyring, generate one; existing rows encrypted with the dummy key are
re-encrypted once (decrypt with dummy, encrypt with new) in `init_db`. Dev keeps the env key.

### D17. Background mode
The package launcher gains `--service`: headless, foreground, writes
`$VISTA_HOME/service.json` `{pid, url, version, started_at}` and removes it on exit. The backend
installs the login item (`services/background_service.py`): a LaunchAgent plist
(`RunAtLoad`, `KeepAlive` on failure), a systemd user unit (`Restart=on-failure`), or a
`schtasks /SC ONLOGON` task. Electron, before starting its supervised launcher, reads
`service.json`, checks the pid and URL answer, and opens a plain window onto that URL (the
existing `--url` path); closing it leaves the service. Installers stop the service
(`launchctl bootout`, `systemctl --user stop`, `schtasks /End`), install, start it again.

### D18. Audit
`slack_audit` rows and `logs/slack-audit.jsonl` lines: time, team, Slack user, VISTA user,
thread, event (request / refused / confirm_posted / confirmed / declined / expired /
tool_result), tool, redacted args, job id. Args pass through a redactor that drops keys
matching token/password/secret/passcode.

## Risks / Trade-offs

- [The model ignores summary-first instructions] → the formatter enforces the summary length.
- [Model calls a destructive tool without annotations] → anything not marked read-only is gated.
- [Lease message deleted or unpinned by the user] → treated as no lease; the instance that
  holds the setting recreates it.
- [Two machines start within the same heartbeat] → after connecting, each re-reads the lease
  after a random 2–8 s delay and the loser disconnects; at most a few events are split.
- [Lid closed on a MacBook sleeps regardless] → documented; needs external display and power.
- [Crash, power loss, network drop give no offline notice] → documented in `docs/slack.md`.
- [Re-encrypting the DB key fails midway] → done in one transaction; on failure keep the old key.
- [Keyring prompts on macOS for an unsigned binary] → the backend's Python is the requester;
  first access may prompt once; documented.

## Migration Plan

Additive tables and columns via `init_db`. Slack is off until configured. The encryption-key
move runs once per packaged install; rollback to an older package would not read the new key,
so the release notes say to keep the data directory backed up before downgrading.
