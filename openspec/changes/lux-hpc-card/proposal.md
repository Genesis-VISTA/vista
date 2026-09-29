## Why

This branch makes Lux a cluster VISTA can submit to (Slurm over SSH, through
`hub.ccs.ornl.gov`), but the NavRail's HPC section only knows Frontier, Odo, and
Perlmutter, so researchers get no sign Lux is there or whether it is up.
`hpc-cards` left Lux out "until its IRI endpoint exists". It still has none, and
waiting for one leaves a usable cluster invisible.

## What Changes

- **Lux joins the supported clusters.** The rail gets a fourth card (Lux, short
  label "Lx", subtitle "OLCF · Slurm over SSH"). Like the others, it is shown by
  default and can be hidden.
- **Lux's facility check is a reachability probe.** The backend opens a TCP
  connection to the Lux hub (the first configured Lux SSH host) and waits for its
  SSH greeting. It sends no credential. Any failure reads as Couldn't verify,
  never Degraded, because a failed probe cannot tell an outage from a network
  that doesn't reach ORNL. The Lux login node behind the hub is not checked, and
  the details say so.
- **Lux's credential check always passes, as information only.** Lux has no
  stored credential; a researcher signs in with a PIN + RSA passcode when a chat
  first uses it. So the card is **Ready** (green) whenever the hub answers. No
  new state is added.
- **No Globus check for Lux.** Its files move over the same SSH connection.
- **The settings modal gets an info-only Lux section**: its "Show in sidebar"
  switch, the project jobs run under, and how sign-in works. Nothing is stored.
- **A `lux-hello` sample job** (`hpc_jobs/lux-hello/`): one node for a few
  seconds, reporting the compute node's hostname and GPUs. It checks sign-in,
  upload, submission, status, and output fetch end to end, without
  `forge-pretrain`'s 16 nodes and FORGE checkout.
- The backend reads Lux's hub and project from the MCP server's own
  `VISTA_MCP_LUX_SSH_HOSTS` / `VISTA_MCP_LUX_ACCOUNT`, as it already does for the
  other clusters' endpoints.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `hpc-availability`: Lux is added to the supported clusters, with its own
  facility check (hub reachability) and an information-only credential check;
  cluster visibility, the settings modal, and the rail's details cover it. This
  capability is introduced by the unarchived `hpc-cards` change, which must be
  archived before this one.

## Impact

- **Backend**: `"lux"` added to the `HpcCluster` literal and the service's
  cluster list. A new SSH-greeting probe in `services/hpc_status.py`, cached
  across researchers. `lux_ssh_hosts` and `lux_account` added to
  `HpcClusterSettings`, kept equal to the MCP defaults by the parity test.
  `GET /users/me/hpc-status` returns a Lux entry. No schema migration: hidden
  clusters are already a JSON list.
- **UI**: `lib/hpc-status.ts`, `HpcStatusSection.tsx`, and
  `UserSettingsModal.tsx` learn Lux, along with their tests and the hermetic
  e2e fixture.
- **MCP server**: no code change; Lux submission is unchanged.
- **HPC jobs**: new `hpc_jobs/lux-hello/`, covered by the existing catalog
  contract tests plus one hermetic submit test.
- **Network**: the backend makes an outbound TCP connection to the Lux hub on
  port 22 once a minute at most while anyone has the rail open.

## Non-goals

- **Lux support in the shared `example` job.** That belongs to a separate
  change; `lux-hello` covers the cheap end-to-end check here.
- **Knowing whether a chat is signed in to Lux.** The SSH session lives in the
  MCP server per chat; the rail does not look at it.
- **Recording login failures.** A mistyped passcode does not mark Lux rejected.
- **A stored Lux username** to pre-fill the sign-in prompt.
- **A data-driven cluster registry.** Lux is a fourth hardcoded branch beside
  the existing three.
