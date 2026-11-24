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

This change installs on Holly by hand with sudo, from `deploy/holly/README.md`: units copied
to `/etc/containers/systemd/` and `/etc/systemd/system/`, podman's `graphroot` on `/scratch`
through `/etc/containers/storage.conf`, the `vista` account, the firewall rule for `:8443`, and
the data dataset and its snapshots. `deploy/holly/ANSIBLE-HANDOFF.md` is a living file, started
in group 1: every host change gets an entry (what, the exact command or file with its repo
source and target, how to check it after a reboot) in the same commit as the step that makes
it, so nothing done by hand exists only on Holly. Group 3 adds `nvidia-fabricmanager` and the
collaboration server's unit, checks it against the README, and it goes to Lawrence. The
README's reinstall steps cover a reboot until his follow-up lands. *Alternative:* Ansible
inside this change, or Lawrence making each host change (rejected by Sam: it would gate the
change on another team's queue; get it working first, keep the record in git).

### D3. Service account and release image

Run the Quadlet with `User=vista` mapping (or rootless under `vista` if `/dev/kvm` and Beam's
loopback allow; checked in group 1). `build-image.sh` takes a release tag and refuses a dirty
tree; the image records the tag for the version route.

### D4. Beam soft

`Wants=vllm-beam.service` and `After=`; drop `Requires=`. The Server provider's availability
probe (`multi-user-hosted` D10) reports Beam down.

### D5. Base path

`build-image.sh --base-path /vista/ui` passes `VISTA_BASE_PATH` to the UI build. O1 is
settled at `/vista/ui` (Sam with Jesse, 2026-11-24); a host of its own later would only build
with an empty base path.

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
needs multi-user-hosted G1, G2 ──► H1 unit + image + handoff file ──► H2 TLS front + firewall (sudo)
ask now ───────────────────────────────────────────► H5 route at /vista/ui ◆ Jesse / Obsidian admins
H1 ──► H4 data + backups (sudo)                    H1 ──► H6 runbook + metrics
multi-user-hosted complete + H1–H6 ──► H7 two-user acceptance through Obsidian ◆ Sam
H7 ──► H3 finish the handoff note ──► ◆ change 2 done ──► follow-up: Ansible (Lawrence)
```

Only the route (H5) is another team's queue inside this change, so it is asked for now; host
changes on Holly are ours, recorded as they are made.

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
units from the README, start, check `/version` through Obsidian. Rollback: stop `vista`, start
the pilot unit from `holly-staging` again.
