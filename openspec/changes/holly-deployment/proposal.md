## Why

`multi-user-hosted` makes VISTA safe for many researchers; this change puts it where they can
reach it. The step 2 pilot (branch `holly-staging`) runs on Holly with host networking, no
login, an SSH tunnel, units on a tmpfs root that a reboot erases, data in Sam's scratch folder,
and a hard dependency on Beam. Researchers should instead open VISTA through Obsidian's gateway
and find their work there after a reboot or an upgrade.

## What Changes

- **Reachable only through the gateway**: every VISTA port on loopback, plus one TLS port that
  only Obsidian may reach (firewall), as the collaboration server's `:8081` has.
- **Working on Holly**, installed by hand from a README: the Quadlet unit and Beam's unit with
  `[Install]`, a service account (like the collab server's `vista`), an image built from a
  tagged release, secrets in a root-owned file outside the data folder. Holly's root is
  tmpfs, so surviving a reboot comes from Ansible in a **follow-up**: this change ends with a
  handoff note listing what Lawrence's Ansible must own.
- **Data kept**: a service-owned folder on `/scratch` with ZFS snapshots or nightly backups of
  `vista.db` and the stores.
- **Beam as a soft dependency** (`Wants=`/`After=`), so freeing the GPUs stops only the server
  model.
- **The Obsidian route**: `/vista/ui` (or a host of its own, O1) with browser-mode
  `openid-connect`, the token passed upstream, no response buffering and long timeouts for
  streams; a Keycloak redirect URI; certificate verification for both Holly upstreams.
- **Operations**: an upgrade runbook (announce, drain, restart; startup recovery resumes
  work) and health and usage in Holly's Prometheus and Grafana.

## Capabilities

### New Capabilities

- `holly-deployment`: how the hosted VISTA is reached, kept running, backed up and upgraded
  on Holly behind Obsidian.

### Modified Capabilities

None.

## Non-goals

- Anything in the application itself (change `multi-user-hosted`).
- Frontier credentials and a Moderate-enclave service (O4).
- Running VISTA on Slate or Kubernetes.
- Holly's Ansible itself: a follow-up, from this change's handoff note.
- VISTAGuard.

## Impact

`deploy/image/` (TLS front, base path build argument, release tag), `deploy/holly/` (units,
env, README runbook, Ansible handoff note), and other teams' queues: Lawrence (firewall, ZFS
snapshots now; Ansible as the follow-up), Jesse's APISIX image repo and the Obsidian Keycloak
admins (route, redirect URI, certificate verification). Acceptance needs `multi-user-hosted`
complete.
