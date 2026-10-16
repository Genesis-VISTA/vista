# Proposal

## Why

A researcher away from their computer has no way to reach the VISTA agent running on it: they
cannot check a queue, submit a job, or learn that one finished. Slack is on their phone already,
and Socket Mode lets a machine with no public address receive Slack events, so VISTA can answer
there with the same agent as the desktop chat.

## What Changes

- A Slack channel to the agent, off by default. DMs and @mentions start or continue a
  conversation; one Slack thread is one VISTA conversation, and it runs through the same agent
  core as the in-app chat.
- Only Slack users linked from the desktop app may act, only in allowlisted workspaces (and
  optionally channels). Linking pairs a code shown in Slack with the desktop app; no secret is
  ever typed into Slack.
- Every Slack-initiated tool that is not read-only waits for a Block Kit confirmation that only
  the requester can give and that expires after 10 minutes. Login forms are never shown in Slack.
- Desktop-only safeguards for Slack-initiated jobs: max nodes, max walltime, max jobs per day,
  and a lock that Slack can set but only the desktop can clear.
- Slack replies lead with a summary, edit one progress message in place, stay within Slack's
  limits, and offer buttons for ambiguous choices.
- VISTA records every job it submits, from chat or Slack, and notifies the requester in Slack
  when a Slack-submitted job ends, also for jobs that ended while VISTA was offline. A setting
  extends this to jobs submitted from the desktop.
- A read-only queue-status tool and a read-only submission preview in the vista MCP server.
- DM reminders 24 h and 2 h before an HPC credential expires; clear messages after it has.
- Offline and back-online DMs on graceful stop and sleep, debounced over 2 minutes.
- An App Home tab with status, host, credential expiry, lock and recent Slack jobs.
- Only one machine handles a Slack app at a time, by a lease held in Slack, with takeover.
- An optional background mode: VISTA's services run as a login service (launchd, systemd user
  unit, Windows scheduled task) and the window becomes a view onto them. Optional sleep
  prevention while Slack is enabled.
- Slack tokens live in the OS secret store. The database encryption key of a packaged install
  moves there too, generated per install, instead of the shared development key.
- An audit log of every Slack-initiated action.
- A Slack app manifest, a setup guide, and a design note for a future hosted relay.

## Capabilities

### New Capabilities

- `slack-integration`: the Slack channel to the agent — identity and access, confirmation,
  safeguards, conversation mapping, formatting, job notifications, credential reminders,
  offline notices, App Home, single active instance, configuration and audit.

### Modified Capabilities

- `desktop-window`: with background mode on, the services outlive the window; a launch
  attaches to the running service instead of starting one.
- `release-builds`: the installers stop and restart VISTA's background service around an
  upgrade instead of refusing.
- `chat-runs`: prompts in a Slack-initiated run expire after 10 minutes, and login forms are
  declined there instead of waiting.
- `hpc-job-submission`: every successful submission is recorded with its origin; queue status
  and a submission preview are available as read-only tools.

## Impact

- Backend: new `vista_backend/slack/` package, `services/conversation.py` shared by the HTTP
  run endpoint and Slack, new tables, `SlackSettings`, lifespan tasks, `api/slack.py`.
- MCP server: `get_hpc_queue_status` and `preview_hpc_job`.
- UI: a Slack section in Settings, a Slack badge on conversations.
- Electron, launchers and installers: background service and attach mode.
- New dependencies: `slack-bolt`, `slack-sdk`, `aiohttp`, `keyring`.
- With Slack off, nothing connects and no Slack code runs; packaged installs gain a
  per-install encryption key either way.
