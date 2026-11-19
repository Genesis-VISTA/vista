# Proposal

## Why

A researcher away from their computer has no way to reach the VISTA agent running on it: they
cannot check a queue, submit a job, or learn that one finished. Slack is on their phone already,
and Socket Mode lets a machine with no public address receive Slack events, so VISTA can answer
there with the same agent as the desktop chat.

## What Changes

- A Slack channel to the agent, off by default. DMs and @mentions start or continue a
  conversation; one Slack thread is one VISTA conversation. Its messages go through the
  conversation's inbox like the in-app chat's, so they queue while a turn runs and are never
  dropped; a queued message is acknowledged with a reaction.
- A conversation belongs to the channel that started it. A Slack conversation appears in the
  desktop app read-only: it can be stopped there, but not written to or answered.
- Only Slack users linked from the desktop app may act, only in allowlisted workspaces (and
  optionally channels). Linking pairs a code shown in Slack with the desktop app; no secret is
  ever typed into Slack.
- Every Slack action that touches a cluster (submitting or cancelling a job) waits for a Block
  Kit confirmation that only the requester can give and that expires after 10 minutes. A tool's
  own Approve/Decline question (a file transfer) is shown as buttons with the same rules. Login
  forms are never shown in Slack. Sandbox work and read-only tools, job watches included, run
  without a card.
- Desktop-only safeguards for Slack-initiated jobs: max nodes, max walltime, max jobs per day,
  checked against what the job would actually run with, and a lock that Slack can set but only
  the desktop can clear.
- Slack replies lead with a summary, edit one progress message in place, stay within Slack's
  limits, and offer buttons for ambiguous choices.
- VISTA records every job it submits, from chat or Slack. A job submitted from a Slack
  conversation is watched (background-tasks), and its end is reported in that thread, also for
  jobs that ended while VISTA was offline. A turn VISTA starts by itself in a Slack conversation
  is posted there, and any card it needs @mentions the conversation's owner.
- A read-only queue-status tool and a read-only submission preview in the vista MCP server.
- DM reminders 24 h and 2 h before an HPC credential expires; clear messages after it has.
- An App Home tab with status, host, credential expiry, lock and recent Slack jobs.
- Slack tokens live in the OS secret store, or in a secrets directory when VISTA runs hosted.
- An audit log of every Slack-initiated action.
- A Slack app manifest and a setup guide published on the docs site. The hosted-relay design note
  is kept in the repository, marked superseded by the facility-service plan, and not published.

### Not in this change

These were part of !163 and move to a follow-up for running Slack from a laptop:

- background mode (a login service that keeps VISTA running with the window closed), the window
  attaching to it, and the installers stopping and restarting it;
- sleep notices and sleep prevention, and the offline/back-online DMs;
- the lease that lets only one machine handle a Slack app;
- moving the packaged database's encryption key into the OS secret store.

## Capabilities

### New Capabilities

- `slack-integration`: the Slack channel to the agent — identity and access, the channel a
  conversation belongs to, confirmation, safeguards, conversation mapping, formatting, job
  notifications, credential reminders, App Home, configuration and audit.

### Modified Capabilities

- `chat-runs`: in a Slack conversation's run, prompts expire after 10 minutes, a prompt offering
  one choice is shown as buttons, and login forms are declined instead of waiting.
- `hpc-job-submission`: every successful submission is recorded with its origin; queue status
  and a submission preview are available as read-only tools.

## Impact

- Backend: new `vista_backend/slack/` package; Slack messages enter through `services/inbox` and
  turns start through `ChatRunRegistry.wake`, which takes the run's channel from the
  conversation's origin; Slack submissions register job watches (`services/background_tasks`);
  new tables, `SlackSettings`, lifespan tasks, `api/slack.py`.
- MCP server: `get_hpc_queue_status` and `preview_hpc_job`.
- UI: a Slack section in Settings, a Slack badge on conversations, and a read-only view of Slack
  conversations.
- New dependencies: `slack-bolt`, `slack-sdk`, `aiohttp`, `keyring`.
- With Slack off, nothing connects and no Slack code runs.
- Runs hosted with `VISTA_BACKEND_DEPLOYMENT=hosted` (secrets directory, no desktop features), for
  the Holly pilot.
