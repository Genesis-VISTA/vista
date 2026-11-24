## Context

See proposal.md. Today (`holly-staging`): rootful podman Quadlet `deploy/holly/vista.container`
with `Network=host`, `Requires=vllm-beam.service`, `/dev/kvm`, data at
`/scratch/baumannsk/vista-holly/data`, env at `…/vista.env`, no `[Install]`. Holly's root is a
tmpfs overlay, so anything under `/etc` not managed by Ansible is gone at reboot. The
collaboration server already runs on Holly as user `vista` (uid 910) and terminates TLS itself
on `:8081` with a self-signed certificate; APISIX reaches it with `proxy_ssl_verify` off.
APISIX routes are baked into its image (standalone mode) in Jesse's repo.

## Goals / Non-Goals

**Goals:** the same container as step 2, closed to Holly, durable, backed up, reachable at an
Obsidian address. **Non-Goals:** high availability, more than one instance, Kubernetes.

## Decisions

### D1. Loopback plus one TLS port

Keep `Network=host` (rootful podman has no pasta; Beam is on host loopback) and rely on
`multi-user-hosted`'s loopback binds and MCP shared secret. A small TLS front in the image
(`deploy/image/tls-front.mjs`, Node's `https` piping to the UI on loopback, ~60 lines, no new
dependency) listens on one port (`:8443`) with a certificate in the secrets folder; the
firewall admits only Obsidian's gateway addresses. *Alternatives:* Caddy or nginx in the image
(another component to patch); a private podman network (Beam on host loopback is then
unreachable without more plumbing).

### D2. By hand now, Ansible as a follow-up

This change installs on Holly by hand from `deploy/holly/README.md`: units copied to
`/etc/containers/systemd/` and `/etc/systemd/system/`, podman's `graphroot` on `/scratch`
through `/etc/containers/storage.conf`, the `vista` account, and the firewall rule (Lawrence
opens `:8443` to Obsidian; it is one rule, needed for the route to work at all). Everything on
the tmpfs root is listed in `deploy/holly/ANSIBLE-HANDOFF.md`: each file with its source in
the repo and target path, the account, the firewall rule, `nvidia-fabricmanager`, and the
collaboration server's unit. The README's reinstall steps cover a reboot until Lawrence's
follow-up lands. *Alternative:* Ansible inside this change (rejected by Sam: it would gate the
change on another team's queue; get it working first).

### D3. Service account and release image

Run the Quadlet with `User=vista` mapping (or rootless under `vista` if `/dev/kvm` and Beam's
loopback allow; checked in group 1). `build-image.sh` takes a release tag and refuses a dirty
tree; the image records the tag for the version route.

### D4. Beam soft

`Wants=vllm-beam.service` and `After=`; drop `Requires=`. The Server provider's availability
probe (`multi-user-hosted` D10) reports Beam down.

### D5. Base path

`build-image.sh --base-path /vista/ui` passes `VISTA_BASE_PATH` to the UI build; empty for a
host of its own. O1's answer picks the value; nothing else changes.

### D6. Route

One APISIX route for the UI path with `openid-connect` in browser mode (`bearer_only` false,
session cookie, `set_access_token_header` true), the same Keycloak client `apisix` so the
collab server accepts the forwarded token, `proxy_buffering off` and a one-hour read timeout,
upstream `https://<holly>:8443`. Batch with `proxy_ssl_verify on` and a Holly certificate with
an IP SAN for both upstreams.

### D7. Backups

ZFS snapshots of the data dataset every night, kept 14 days, plus a weekly `sqlite3 .backup`
copy to `/proj` for off-box restore. Restore steps in the README, rehearsed once.

## Delivery plan

```
needs multi-user-hosted G1, G2 ──► H1 unit + image ──► H2 TLS front (+ firewall rule ◆ Lawrence)
ask now ───────────────────────────────────────────► H5 route ◆ Obsidian admins (O1, redirect URI, cert)
H1 ──► H4 data + backups (◆ Lawrence for ZFS)      H1 ──► H6 runbook + metrics
multi-user-hosted complete + H1–H6 ──► H7 two-user acceptance through Obsidian ◆ Sam
H7 ──► H3 Ansible handoff note for Lawrence ──► ◆ change 2 done ──► follow-up: Ansible (Lawrence)
```

The route request (H5) and the firewall rule are asked for as soon as the change is approved,
because they are other people's queues; the Ansible work waits for the handoff note.

## Risks / Trade-offs

- [The route's rollout affects every tenant on Obsidian] → batch it with the certificate fix,
  in one MR to Jesse's repo, tested on a staging tenant first if one exists.
- [A Node TLS front mishandles SSE or websockets] → a streaming test through it in group 2.
- [Rootless under `vista` loses `/dev/kvm` or Beam] → group 1 checks; fall back to rootful with
  `User=` and a userns mapping.
- [Backups of a live SQLite file are torn] → ZFS snapshots are atomic; the `/proj` copy uses
  SQLite's backup API.

## Migration Plan

Fresh data folder (pilot data stays where it is). Cut-over: stop the pilot unit, install the
Ansible-managed units, start, check `/version` through Obsidian. Rollback: stop `vista`, start
the pilot unit from `holly-staging` again.

## Open Questions

- O1 (path or host), answered by the Obsidian admins; it sets only a build argument (D5).
