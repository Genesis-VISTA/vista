<!--
Groups run through apply-in-groups-delegate where they are code; groups marked EXTERNAL are
requests to other teams, tracked on the dashboard. Nothing here runs in PR CI unless stated.
-->

## 1. Unit and image (needs multi-user-hosted groups 1, 2)

- [ ] 1.1 `vista.container`: `Wants=`/`After=vllm-beam.service` instead of `Requires=`; `[Install] WantedBy=multi-user.target`; data and secrets paths as Ansible variables; verify with `systemd-analyze verify` on the generated unit (on Holly, hands-on)
- [ ] 1.2 Run as the `vista` service account; check `/dev/kvm` and Beam's loopback under it, and record the outcome in the README; verify by starting the unit as `vista` on Holly (hands-on)
- [ ] 1.3 `build-image.sh` takes a release tag, refuses a dirty tree, passes `--base-path` to the UI build, and records the tag for the version route; verify by building and calling `/version`
- [ ] 1.4 `vista.env.example` split into non-secret env and a root-owned secrets file (encryption key, MCP secret, Slack tokens, server model key); verify the hosted startup refusal from multi-user-hosted 1.3 fires when the secrets file is missing

## 2. TLS front (needs 1)

- [ ] 2.1 `deploy/image/tls-front.mjs` on `:8443` piping to the UI on loopback, certificate from the secrets folder (self-signed made on first start, as the collab server's `launch.sh` does); verify with a test in `deploy/image/` that streams SSE and a websocket through it (runs in PR CI)

## 3. Ansible handoff — EXTERNAL: Lawrence (needs 1, 2)

- [ ] 3.1 Send Lawrence the units, `storage.conf` (graphroot on `/scratch`), firewall rule (`:8443` from Obsidian only), `nvidia-fabricmanager`, the `vista` account and the collab server's unit; verify when Holly reboots and both services come back on their own

## 4. Data and backups (needs 1; ZFS EXTERNAL: Lawrence)

- [ ] 4.1 Service-owned dataset on `/scratch` for VISTA's data; ask Lawrence for nightly snapshots kept 14 days; verify by listing snapshots after two nights
- [ ] 4.2 Weekly SQLite backup-API copy to `/proj` and restore steps in `deploy/holly/README.md`; verify by restoring onto an empty folder and starting VISTA (hands-on)

## 5. Obsidian route — EXTERNAL: Jesse's APISIX image repo, Obsidian Keycloak admins (ask now)

- [ ] 5.1 Ask O1 (`/vista/ui` or a host of its own) and request the route per design D6 with the Keycloak redirect URI, batched with `proxy_ssl_verify on` for both Holly upstreams; verify by the admins' answer recorded on the dashboard
- [ ] 5.2 Route live; verify by signing in at the Obsidian address and seeing an empty project list (hands-on)

## 6. Runbook and metrics (needs 1)

- [ ] 6.1 Upgrade runbook in `deploy/holly/README.md` (announce, wait for turns, restart, check recovery); verify by rehearsing it once with a watched dry-run job (hands-on)
- [ ] 6.2 A metrics endpoint (up, active users, runs, errors) scraped by Holly's Prometheus and a Grafana panel; verify a backend test for the endpoint (PR CI) and the panel showing a stop (hands-on)

## 7. Two-user acceptance (needs multi-user-hosted complete, 1–6)

- [ ] 7.1 Two researchers sign in through Obsidian; check isolation, an Odo job with a watch, Slack link, collaboration publish, Beam stopped and started; verify with a written checklist ticked by Sam (hands-on, not in PR CI)
