# Slack

Talk to VISTA from Slack, on a phone or a desktop, while VISTA runs on your own machine. It is
the same agent as the app: the same projects, tools, skills and HPC clusters, with your VISTA
session doing the work. Slack is **off by default**, and every setting lives in the desktop app
under **Settings → Slack**, never in Slack.

What Slack never gets: a cluster credential. Jobs run with the VISTA session already on your
machine, and VISTA never asks for a password, passcode, MFA code or token in Slack. Anything
that needs one (a Lux login, connecting Globus) happens in the desktop app.

## How it connects

VISTA uses Slack's **Socket Mode**: the machine running VISTA opens an outbound WebSocket to
Slack, so there is no public URL, no port to open, and nothing has to reach your machine. Slack
signs every event and Bolt verifies it before VISTA sees it.

The consequence is that VISTA answers only while it is running and online. A message sent
while it is off is not queued anywhere; VISTA posts a notice when it goes offline and when it
is back, and Slack itself shows the app as away. A hosted relay that could hold messages is a
design note only: [slack-relay.md](slack-relay.md).

## Setting up

You need to be allowed to create apps in the workspace (or ask an admin to do this part).

1. **Create the app.** At <https://api.slack.com/apps>, choose *Create New App → From an app
   manifest*, pick the workspace and paste
   [`backend/src/vista_backend/slack/manifest.yaml`](../backend/src/vista_backend/slack/manifest.yaml).
   It asks for DMs, @mentions, the Home tab, and the few scopes the code calls; it cannot read
   channels VISTA isn't mentioned in.
2. **Install it** to the workspace (*Install App*), and copy the **Bot User OAuth Token**
   (`xoxb-…`).
3. **Make an app-level token.** Under *Basic Information → App-Level Tokens*, generate one with
   the `connections:write` scope and copy it (`xapp-…`). This is what Socket Mode connects with.
4. **In VISTA, Settings → Slack:**
   - paste both tokens. They go to the system keychain (macOS Keychain, Windows Credential
     Manager, the Secret Service on Linux) and are never shown again or written to a config
     file or log. On a machine with no keychain, Slack can't be turned on, and the section says
     why. On macOS the first access may ask once whether VISTA's Python may use the keychain.
   - add the **workspace ID** (`T…`, from the workspace's URL or *About this workspace*) to the
     allowed workspaces. Events from any other workspace are dropped without a reply.
   - optionally list **channel IDs** (`C…`). If you do, @mentions anywhere else are ignored.
     DMs are always allowed.
   - turn on **Slack** and **Handle Slack on this machine**. The status line should say
     *Connected*.
5. **Link your Slack account.** DM VISTA `link`. It replies with a 6-digit code; enter it under
   *Your linked Slack accounts* within 10 minutes. Each link can pick which project new Slack
   conversations start in, and whether job notices come in the job's thread or a DM.

Only linked accounts can use VISTA. Anyone else who messages it is told, politely and at most
once in a while, that their account isn't linked and how to link it. A link connects a Slack
account to the VISTA user who entered the code, so only that person can complete it.

## Using it

- **DM VISTA**, or **@VISTA** in a channel it has been invited to. A thread is one
  conversation: replies in the thread continue it, and a new message starts a new one. Slack
  conversations appear in the desktop app's chat list with a Slack badge.
- VISTA acknowledges at once and keeps one "Working on it…" message up to date with what it is
  doing, then replaces it with the answer: a short summary first, the rest behind **Show more**.
  Long output and logs are split to fit Slack's limits rather than cut off.
- Ask about your jobs in plain words: "my last job", "the Frontier job from this morning",
  "show me its log". Logs are posted only when you ask.
- When a request is ambiguous, VISTA may offer up to five buttons to choose from.
- When a job you started from Slack reaches a terminal state (completed, failed, cancelled,
  timed out), VISTA tells you, in a DM or in the job's thread. Jobs that finished while VISTA
  was off are reported when it comes back. *Tell me in Slack about jobs started here* adds
  jobs started in the desktop app.
- The **Home tab** shows whether VISTA is connected and since when, whether Slack is locked,
  your recent jobs, and which clusters' credentials are expiring. It says when it was last
  updated, since Slack keeps showing the last version while VISTA is offline.

### Confirmations

Reading runs straight away: answering questions, job status, outputs, logs, queue status. Any
action that changes something (submitting or cancelling a job, writing files, anything a tool
does not declare read-only) is posted as a card with **Confirm** and **Cancel** first. For a job
submission the card shows what would actually be submitted (cluster, job, nodes, walltime,
account), worked out by VISTA rather than described by the model.

- Only the person who asked can confirm; anyone else's tap is refused.
- A card expires after 10 minutes and is then treated as Cancel.
- Before it runs, the confirmed action is checked again against the lock and the limits.

### Limits and the lock

Under *Limits for Slack requests* you set the most nodes per job, the longest walltime and the
number of jobs per day that Slack may submit. A request over any limit is refused before it is
offered for confirmation. Jobs started in the app are not limited. These are set only in the
app, so a lost or borrowed phone can't raise them.

Say **lock** to VISTA in Slack (DM or @mention) to block every action that changes something
until you unlock it. Questions still work. Unlocking is only possible with the *Locked* switch
in the desktop app; saying `unlock` in Slack just says so.

### Credentials

VISTA reminds you in a DM when a cluster credential will expire within 24 hours, and again
within 2 hours: S3M tokens (Odo, Frontier), the NERSC token, and the Globus session (an
estimate, three days after connecting, which refreshing doesn't extend). Renew it in the
desktop app. After it expires, VISTA tells you which actions are unavailable on that cluster
rather than trying them.

A Lux login is an hour-long SSH login made in the desktop app. Slack can use it while it lasts;
when there is none, VISTA says to log in from the app, and job notices for Lux wait until then.

## Away from your desk

VISTA can only answer while it runs, so for Slack to reach it while you are away:

- **Run VISTA in the background** (*Notifications and power*) registers a login item that
  starts VISTA's services at login with no window and keeps them running after the window
  closes: a LaunchAgent on macOS, a systemd user unit on Linux, a scheduled task on Windows. It
  takes effect at your next login; opening VISTA then opens a window onto the running service.
  Turning it off stops the service and removes the item. It is only available in an installed
  package, and on Linux it needs systemd. Upgrading with the installer stops the service and
  starts it again afterwards.
- **Keep this machine awake** prevents idle sleep while Slack is connected (`caffeinate` on
  macOS, `systemd-inhibit` on Linux, `SetThreadExecutionState` on Windows).

Limits worth knowing:

- **A laptop with its lid closed sleeps** regardless of these settings, unless it is on power
  with an external display. VISTA posts an offline notice as it goes to sleep where the OS lets
  it, and a back-online notice when it wakes.
- **A crash, power loss or dropped network sends no offline notice**, since nothing is left to
  send it. Slack shows the app as away, and VISTA posts the back-online notice only after an
  offline notice it sent itself.
- Notices are debounced: VISTA says it is back at most once every 2 minutes, so a flaky
  connection doesn't flood your DMs.

## One machine at a time

All machines using the same app token would share Slack's events between them, so only one
answers. *Handle Slack on this machine* turns that on; the machine that has Slack keeps a
pinned *VISTA status* message in your DM with VISTA, and another machine that finds it fresh
stays on standby and says which host has Slack. **Handle Slack here instead** takes over; the
other machine sees it within a minute and lets go. Edits to that message notify no one.

## Audit log

Every Slack request, refusal, confirmation card, confirm, cancel, expiry and tool result is
recorded with the time, workspace, Slack user, VISTA user, thread, tool and job id, in the
database and in `<data dir>/logs/slack-audit.jsonl`. Arguments are redacted first: any key
naming a token, password, secret or passcode is dropped. Tokens never appear in any log.

## Troubleshooting

- *Can't connect*: the detail under the status says what Slack answered. `invalid_auth` is a
  wrong or revoked bot token; `not_allowed_token_type` is the two tokens swapped.
- *Standing by*: another machine has Slack. Take over, or turn off *Handle Slack on this
  machine* on the other one.
- *Nothing happens on an @mention*: invite VISTA to the channel (`/invite @VISTA`), and check
  the channel allowlist.
- *"Your account isn't linked"*: DM `link` and enter the code in the desktop app.

## For developers

The code is `backend/src/vista_backend/slack/`. Slack is reached only through the
`SlackTransport` interface (`transport.py`); `socket_mode.py` is the Bolt implementation, and
tests use `tests/slack/fakes.py`'s `FakeTransport`, with every real Web API call made to fail.
`tests/slack/` mocks Slack and HPC alike and never submits a job. The design and its decisions
are in `openspec/changes/slack-channel/`.

Before a release, run the manual checklist in
[`openspec/changes/slack-channel/mobile-checklist.md`](../openspec/changes/slack-channel/mobile-checklist.md)
on iOS and Android.
