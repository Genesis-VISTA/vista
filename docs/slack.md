# Slack

Talk to VISTA from Slack, on a phone or a desktop, while VISTA runs on your own machine. It is
the same agent as the app: the same projects, tools, skills and HPC clusters, with your VISTA
session doing the work. Slack is **off by default**, and every setting lives in the desktop app
under **Settings → Slack**, never in Slack.

What Slack never gets: a cluster credential. Jobs run with the VISTA session already on your
machine, and VISTA never asks for a password, passcode, MFA code or token in Slack. Anything
that needs one (an S3M or NERSC token, connecting Globus) happens in the desktop app.

## How it connects

VISTA uses Slack's **Socket Mode**: the machine running VISTA opens an outbound WebSocket to
Slack, so there is no public URL, no port to open, and nothing has to reach your machine.
Events arrive only over that connection, which VISTA opened with your app-level token. If the
connection drops, VISTA connects again by itself.

The consequence is that VISTA answers only while it is running and online. A message sent
while it is off is not queued anywhere, and Slack shows the app as away. When VISTA quits, its
Home tab says it is offline.

## Setting up

You need to be allowed to create apps in the workspace. In some workspaces an admin has to
approve a new app before it can be installed; if *Install* asks you to request approval, ask
a workspace admin.

1. **Create the app.** At <https://api.slack.com/apps>, choose *Create New App → From an app
   manifest*, pick the workspace and paste the manifest below (it is
   [`backend/src/vista_backend/slack/manifest.yaml`](../backend/src/vista_backend/slack/manifest.yaml)).
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
     why. macOS may ask whether VISTA may use the keychain; choose *Always Allow*.
   - add the **workspace ID** (`T…`, from the workspace's URL or *About this workspace*) to the
     allowed workspaces. Slack can't be turned on without one, and events from any other
     workspace are dropped without a reply.
   - optionally list **channel IDs** (`C…`). If you do, an @mention anywhere else gets a reply
     only its sender sees, *I don't work in this channel*, and nothing more. DMs, and the
     buttons in them, are always allowed.
   - turn on **Slack**. The status line should say *Connected*. Saving any Slack setting other
     than the lock and the limits reconnects, and expires any confirmation card still open.
5. **Link your Slack account.** DM VISTA `link` (in a DM, as the whole message). It replies with
   a 6-digit code; enter it under
   *Linked Slack accounts* within 10 minutes. Each link can pick the project new Slack
   conversations start in; *Ask each time* (the default) has VISTA ask in each new thread, unless
   you have only one project.

Only linked accounts can use VISTA. Anyone else who messages it is told, politely and at most
once in a while, that their account isn't linked and how to link it.

A VISTA has one researcher, so a link admits a Slack account to *this* VISTA, and that account
then acts as you, with your cluster credentials and your projects. Only someone at the desktop
app can enter the code, so only you can complete a link. Link only your own accounts: one Slack
app serves one researcher's VISTA.

### The manifest

Changing a scope means updating the app in Slack (*App Manifest*, then reinstall it from the
banner Slack shows).

```yaml
# The Slack app VISTA talks through. Create it at https://api.slack.com/apps
# → Create New App → From an app manifest, and paste this file; see docs/slack.md.
#
# Socket Mode only: VISTA opens the connection from the researcher's machine,
# so the app has no request URL and nothing needs to reach that machine.
# The scopes are exactly what slack/socket_mode.py calls; keep them in step.
display_information:
  name: VISTA
  description: Ask VISTA about molten salts and your HPC jobs, from anywhere.
  background_color: "#1d2433"
features:
  app_home:
    home_tab_enabled: true
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: VISTA
    always_online: false
oauth_config:
  scopes:
    bot:
      - app_mentions:read # @VISTA in a channel
      - chat:write # replies, the "working" message, confirmation cards, reminders
      - im:history # DMs to VISTA
      - im:read
      - im:write # opening a DM for credential reminders
      - files:write # the figures and files a turn displayed
      - reactions:write # ⏳ on a message that waits, ✅ when a turn takes it
settings:
  event_subscriptions:
    bot_events:
      - app_home_opened
      - app_mention
      - message.im
  interactivity:
    is_enabled: true
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

### One machine at a time

Turn Slack on in one VISTA only. Two machines connected with the same app token would each get
a share of Slack's events, so some messages would be answered by the other machine, and some
by neither. To move Slack to another machine, turn it off on the first one.

## Using it

- **DM VISTA**, or **@VISTA** in a channel it has been invited to. A thread is one
  conversation: replies in the thread continue it, and a new message starts a new one. In a
  channel, @mention VISTA in each reply too: it sees only the messages that mention it there.
  Only the person who started a thread can continue it.
- If you have no project yet, or no model is set up in the desktop app, VISTA says so instead
  of answering.
- **Which project:** with no project chosen for your link and more than one project, the first
  message of a new thread is answered with *Which project should this go in?* and a button per
  project (a menu past five). Messages you send before tapping wait, and are answered in the
  project you pick. Only you can pick, and the choice holds for that thread only.
- VISTA keeps one "Working on it…" message up to date with what it is doing, then replaces it
  with the answer: a short summary first, the rest behind **Show more**, which expands the
  answer in place. Long output is split to fit Slack's limits rather than cut off.
- **Messages while it works** wait for the turn and are taken at its next step, in order, with
  none dropped: VISTA marks each with ⏳ when it arrives and ✅ when a turn takes it.
- **Figures and files** the agent displays are attached in the thread after the answer, up to
  10; past that the thread says how many more are in the desktop app.
- Ask about your jobs in plain words: "my last job", "the Frontier job from this morning",
  "show me its log". When a request is ambiguous, VISTA may offer up to five buttons to choose
  from.
- **A job submitted from Slack with `submit_hpc_job` is watched** (an `agenthpc` campaign's
  parameter sets are not). When it ends (completed, failed, cancelled, timed out), VISTA answers
  in its thread, @mentioning you. A job that ended while VISTA was off is
  reported when it is back. A conversation in the desktop app never posts to Slack.
- The **Home tab** shows whether VISTA is connected, on which machine and since when, whether
  Slack is locked, the Slack limits, your five most recent jobs from Slack, and when each known
  cluster credential expires or expired. It says when it was last updated, since Slack keeps
  showing the last version while VISTA is offline.

### On the desktop

A conversation started in Slack appears in the desktop app's chat list with a Slack badge, and
is **read-only** there: you can read it and watch its turn, and **Stop** it, but you can't send
it a message, change its waiting messages or answer its prompts (the backend refuses them
too). It continues in its Slack thread. Messages a Stop leaves unanswered lose their ⏳, and the
thread says so.

### Confirmations

**Submitting or cancelling a job** is posted as a card with **Confirm** and **Cancel** first:
`submit_hpc_job` and `cancel_hpc_job`, and the `agenthpc` campaign's submissions and
cancellations. Everything else runs straight away once the lock and the limits allow it:
reads, file writes, shell commands. The card shows what would actually happen: for a
submission, the cluster, account (your S3M token's project on Odo, Frontier and Lux), nodes,
walltime and script, worked out by VISTA with the job's defaults applied, rather than described
by the model.

- Only the person who asked can confirm; anyone else's tap is refused.
- A card expires after 10 minutes and is then treated as Cancel.
- Before it runs, the confirmed action is checked again against the lock and the limits.
- In a turn VISTA started itself, such as after a watched job ended, the card @mentions you.
- A question with fixed answers from a tool, such as a file transfer's **Approve** / **Decline**,
  is shown as buttons the same way. Any other form (a login) or link is declined at once, and
  the agent says to do it in the desktop app.

### Limits and the lock

Under *Limits for Slack requests* you set *Most nodes per job*, *Longest walltime (minutes)* and
*Jobs per day* (counted over any 24 hours) that Slack may submit; they start at 4 nodes, 120
minutes and 5 jobs. They are checked against the job as it would be submitted, its defaults
included, before it is offered for confirmation: a job over a limit gets no card, and the agent
is told why. An `agenthpc` parameter set's nodes and walltime are fixed by its application on
the cluster, so it is held to the lock and to the count of jobs submitted, which it doesn't add
to. Jobs started in the app are not limited. These are set
only in the app, so a lost or borrowed phone can't raise them.

Say **lock** to VISTA in Slack (DM or @mention, as the whole message) to block every action
that changes something until you unlock it. Questions still work. Unlocking is only possible with the *Locked* switch
in the desktop app; saying `unlock` in Slack just says so.

### Credentials

VISTA reminds you in a DM when a cluster credential will expire within 24 hours, and again
within 2 hours: S3M tokens (Odo, Frontier, Lux), the NERSC token, and the Globus session (an
estimate, three days after connecting, which refreshing doesn't extend). Renew it in the
desktop app. After it expires, VISTA tells you which actions are unavailable on that cluster
rather than trying them.

## Audit log

Every Slack request, `lock`, refusal by the lock, the limits or an expired credential,
confirmation card and how it closed (confirmed, cancelled, expired, refused), and each job
submitted from Slack with its job id, is recorded with the time, workspace and Slack user
(where known), collaboration-server identity (when known), conversation, tool and job id; a
request also records its channel and thread. It is kept in the database and in
`<data dir>/logs/slack-audit.jsonl`. Values under any key naming a token, password, secret,
passcode, OTP or MFA are replaced with `[redacted]`, and anything shaped like a Slack token, a
JWT or a bearer token is masked wherever it appears. VISTA never logs the tokens themselves.

## Troubleshooting

- *Can't connect*: the detail under the status says what Slack answered. *Slack rejected the
  bot token* means a wrong or revoked bot token: reinstall the app and paste a fresh one.
  `invalid_auth` from the connection itself is the app-level token. VISTA tries again every
  minute.
- *Some messages get no answer*: another machine may have Slack on with the same app token.
  Turn it off there.
- *Nothing happens on an @mention*: invite VISTA to the channel (`/invite @VISTA`), and check
  the channel allowlist.
- *"Your account isn't linked"*: DM `link` and enter the code in the desktop app.

## For developers

The code is `backend/src/vista_backend/slack/`. Slack is reached only through the
`SlackTransport` interface (`transport.py`); `socket_mode.py` is the Bolt implementation, and
tests use `tests/slack/fakes.py`'s `FakeTransport`, with every real Web API call made to fail.
`tests/slack/` mocks Slack and HPC alike and never submits a job. The design and its decisions
are in `openspec/changes/slack-channel/`. The manifest above is checked against
`manifest.yaml` by `tests/slack/test_manifest.py`.

Before a release, run the manual checklist in
[`openspec/changes/slack-channel/mobile-checklist.md`](../openspec/changes/slack-channel/mobile-checklist.md)
on iOS and Android.

### Running VISTA hosted

`VISTA_BACKEND_DEPLOYMENT=hosted` is for a headless VISTA serving one researcher from a server.
Its one effect on Slack is where the tokens live: one file per secret in `<data dir>/secrets`, or
`VISTA_BACKEND_SECRETS__DIRECTORY`, instead of the OS keychain. The directory must be mode
`0700`, or VISTA refuses to use it, and an operator may provide a secret by mounting a file of
that name. `VISTA_BACKEND_SECRETS__BACKEND` (`os`, `directory`) overrides the choice either way.
