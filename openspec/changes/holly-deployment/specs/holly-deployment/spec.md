## Purpose

How the hosted VISTA runs on Holly behind Obsidian's gateway: who can reach it, how it
survives reboots and upgrades, and how researchers' data is kept.

## ADDED Requirements

### Requirement: Reachable only through the gateway

On Holly, VISTA's UI, backend and MCP server SHALL listen on loopback only. The one port open
beyond loopback SHALL serve TLS and SHALL accept connections only from Obsidian's gateway
addresses. Researchers SHALL reach VISTA only at its Obsidian address, after signing in.

#### Scenario: Another Holly user

- **WHEN** another user on Holly connects to the backend's or MCP server's port
- **THEN** the connection is refused or carries no data without the shared secret and a verified token

#### Scenario: From the facility network

- **WHEN** a machine other than Obsidian's gateway connects to VISTA's TLS port
- **THEN** the firewall drops it

### Requirement: Signed in through the browser

The Obsidian route SHALL sign a researcher in with Keycloak in the browser and keep a session,
and SHALL pass the access token upstream on every request, including streams. Streams SHALL
not be buffered and SHALL stay open for at least an hour.

#### Scenario: First visit

- **WHEN** a researcher with the `apisix/vista` role opens VISTA's Obsidian address
- **THEN** they sign in with Keycloak, return to VISTA, and see their projects

#### Scenario: A long chat turn

- **WHEN** a chat turn streams for 20 minutes
- **THEN** events arrive as they are produced and the stream is not cut by the gateway

### Requirement: Installed from a runbook, with a handoff for reboots

The deployment SHALL be installable on Holly by following `deploy/holly/README.md` alone, and
everything it puts outside `/scratch` and `/proj` (units, podman storage settings, firewall
rule, service account, secrets location) SHALL be listed in a handoff note for Holly's
configuration management, so that a follow-up can make VISTA come back on its own after a
reboot. Until then, a reboot SHALL need only the runbook's reinstall steps: data, secrets
and the image SHALL survive it on `/scratch`.

#### Scenario: Fresh install from the runbook

- **WHEN** an operator follows the README on Holly from a tagged release
- **THEN** VISTA answers at its Obsidian address, with no step outside the README

#### Scenario: Reboot before the follow-up

- **GIVEN** researchers' projects and a watched Odo job
- **WHEN** Holly reboots and the operator runs the README's reinstall steps
- **THEN** the projects are there and the watch resumes

#### Scenario: The handoff is complete

- **WHEN** the handoff note's items are applied by configuration management and Holly reboots
- **THEN** VISTA comes back at the same address with no step by hand

### Requirement: Runs from a release as a service account

The deployed image SHALL be built from a tagged release, run as a dedicated service account,
and read its encryption key and server secrets from a file readable only by root and the
service, outside the data folder.

#### Scenario: Which version is running

- **WHEN** an operator asks the version route
- **THEN** it names a release tag, not a working-copy build

### Requirement: Data is backed up

VISTA's data folder SHALL be service-owned on `/scratch` and SHALL be snapshotted or backed up
at least nightly, with a documented, tested restore.

#### Scenario: Restore

- **WHEN** an operator follows the restore steps from the last backup onto an empty folder
- **THEN** VISTA starts with every researcher's projects and settings as of that backup

### Requirement: The server model can stop without VISTA

Stopping Beam SHALL NOT stop VISTA. Researchers SHALL see the server model as unavailable and
keep working with their own keys and everything else.

#### Scenario: Beam stopped for GPU work

- **WHEN** an operator stops Beam
- **THEN** VISTA keeps serving, and its running job watches continue

### Requirement: Upgrades do not lose work

An upgrade SHALL follow a runbook that announces it, waits for running turns, restarts the
service, and relies on startup recovery for queued messages and job watches.

#### Scenario: Upgrade with a watched job

- **GIVEN** a researcher's Odo job being watched
- **WHEN** VISTA is upgraded by the runbook
- **THEN** the watch resumes after the restart and reports the job's result

### Requirement: Health is visible

The deployment SHALL export whether it is up, active users, runs and errors to Holly's
Prometheus, with a Grafana panel.

#### Scenario: VISTA down

- **WHEN** VISTA stops answering
- **THEN** the Grafana panel shows it down within five minutes
