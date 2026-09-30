## Context

The HPC cards (`hpc-cards`, on `main`, unarchived) are hardcoded per cluster.
The cluster list appears three times:
- `CLUSTERS` / `_TITLES` in `backend/src/vista_backend/services/hpc_status.py`;
- the `HpcCluster` literal in `backend/src/vista_backend/db/schemas.py`, which
  also validates `hpc_hidden_clusters` writes;
- `HpcCluster` / `HPC_CLUSTERS` / `HPC_CLUSTER_TITLES` in `ui/lib/hpc-status.ts`.

Per-cluster behaviour is written as `cluster == "…"` branches in
`HpcStatusService` (`_cluster`, `_credential_fingerprint`, `_iri_url`, the
facility and credential checks), in `HpcStatusSection.tsx` (`SUBTITLES`,
`SHORT`, `credentialRow`), and in `UserSettingsModal.tsx` (`CREDENTIAL_FIELDS`,
per-cluster JSX).

On this branch Lux is reached only over SSH: `hub.ccs.ornl.gov`, then
`login1.lux.olcf.ornl.gov`, with a PIN + RSA passcode asked through MCP
elicitation and the connection cached per chat (`vista_mcp_server/lib/ssh.py`).
Its settings are `lux_ssh_hosts` and `lux_account` in the MCP server's
`config.py`. From an ORNL-network laptop on 2026-09-29, the hub's port 22
answered and the login node's did not; the login node is only reachable through
the hub.

`resolve_state` already returns `ready` when every applicable check is `ok` and
nothing earlier in `_PRECEDENCE` matches. A check whose reason is `unreachable`
already resolves to `unverifiable` (Couldn't verify).

## Goals / Non-Goals

**Goals:**
- Lux gets a card that is green exactly when its hub answers, with no new state,
  reason, or dot style.
- The probe is cheap, sends no credential, and is identifiable in the hub's logs.
- Hermetic tests cover the probe with no network; one live test probes the real
  hub.

**Non-Goals:**
- Generalising the cluster code into descriptors. Lux is a fourth hardcoded
  branch.
- Anything in the MCP server. Lux submission and its settings are unchanged.

## Decisions

### D0. A `lux-hello` sample job ships with the card
The card proves only that the hub answers. The one Lux job on this branch,
`forge-pretrain`, defaults to 16 nodes × 8 GPUs and clones FORGE on the login
node first. So there has been no cheap way to check that sign-in, upload,
`sbatch`, status, and output fetch work.

`hpc_jobs/lux-hello/` is a Lux-only job with one node and five minutes. It has
no `setup_lux.sh` and no dependencies. It writes the compute node's hostname,
the date, and `rocm-smi` output to `$VISTA_OUT/hello.txt`. Its README becomes
part of the agent's job list like any other, so "check Lux works" has an
obvious tool call. It needs no dispatcher change: `_submit_lux_job` already
accepts any job with a `lux` section and a `job.lux.slurm`.

*Alternatives:*
- **Lux support in the shared `example` job.** It stays a separate change,
  because `example` is also the live smoke test's job on the IRI clusters and
  depends on matplotlib.
- **Keep it as a throwaway local job.** Rejected: the team would have no
  low-cost way to check Lux. If they don't want it, it's one folder to delete.

### D1. Lux is a fourth hardcoded branch
Add `"lux"` to the three cluster lists and a `lux` branch wherever the code
already branches per cluster. Lux goes last in `CLUSTERS`, so the rail order
becomes Frontier, Odo, Perlmutter, Lux.

*Alternative:* a per-cluster descriptor (credential kind, uses-Globus, facility
kind). Rejected for now: DOE has few HPC facilities, so a fourth branch is
cheaper than the refactor.

### D2. The facility probe reads the SSH greeting and sends one line back
`_lux_facility` does the following:
1. Opens `asyncio.open_connection(host, 22)`, where `host` is
   `settings.lux_ssh_hosts[0]` (the hub).
2. Reads one line, at most 255 bytes, within `HTTP_TIMEOUT` (5 s).
3. Passes if the line starts with `SSH-`.
4. Writes `SSH-2.0-VISTA_status_probe\r\n`, then closes the connection.

This uses the standard library only. `asyncssh` is not needed without key
exchange or authentication.

Sending an identification line matters for the hub's logs. A client that
connects and leaves silently is logged by sshd as "did not receive
identification string", the pattern scanners produce and that fail2ban-style
filters match. Named, the probe reads as a client that hung up before key
exchange.

The probe is injectable (`lux_probe: LuxProbe = probe_ssh_greeting`), the same
way `globus_probe` is, so unit tests need no socket.

*Alternatives:*
- TCP connect alone. It can't tell sshd from a firewall that accepts and then
  drops connections.
- A full `asyncssh` connect without auth. It's heavier, triggers a
  keyboard-interactive prompt, and still proves nothing about the login node.

### D3. Every probe failure is `unreachable`
DNS failure, connection refused, timeout, or a first line that is not `SSH-`
all give `Check(ok=False, reason="unreachable", message=…)`. The message names
the host and the failure ("hub.ccs.ornl.gov:22 timed out after 5 s").
`_PRECEDENCE` maps `unreachable` to Couldn't verify, so no new reason is
needed.

*Alternative:* `degraded` (amber). Rejected: a hosted deployment off the ORNL
network would show Lux as down permanently.

### D4. The credential check is a constant `ok`
`_lux_credential` returns `Check(ok=True, project=settings.lux_account,
message="Sign in with PIN + RSA passcode when a chat first uses Lux")` and makes
no call. `credentialRow` already renders `project` as "Project …" on a passing
check. Only its title changes, to "Sign in from a chat" for Lux. Update the
`Check.project` docstring from "the S3M token's project" to "the project the
cluster's jobs run under".

### D5. The probe is cached per deployment; the per-user result cache is unchanged
- **Probe cache:** the result is kept per host for `FACILITY_TTL` (60 s), shared
  across researchers, the same way `_facility_feeds` is. `fresh=True` bypasses
  it.
- **Per-user cache:** the `(user, cluster)` result cache stays as it is.
  `_credential_fingerprint` returns a constant for Lux, since no credential
  feeds its checks.
- **Load on the hub:** at most one connection per minute per backend while
  anyone has the rail open, plus one per Recheck.

### D6. The probed hub travels as `Check.host`

> **Dropped after archive (2026-09-29).** Nothing read `host` once the Lux
> settings box was removed, so the field was taken out; the facility check's
> `message` still names the hub.

Add `host: str | None` to `Check` (backend model and the `HpcCheck` TS type),
set only by Lux's facility check, so the hub is a value in the response rather
than only words in `message`. The project already travels in
`credential.project` (D4). The UI shows the hub through the facility row's
message; nothing in it reads `host` yet.

Lux's settings section holds only its show/hide switch: sign-in is described
where it's needed, in the card's details, and a hidden Lux has no status entry
to read hub or project from anyway.

### D7. Backend settings mirror the MCP's, including comma-separated parsing
Add to `HpcClusterSettings`:
- `lux_ssh_hosts: list[str] = ["hub.ccs.ornl.gov", "login1.lux.olcf.ornl.gov"]`
- `lux_account: str = "stf218"`

Both are read as `VISTA_MCP_LUX_SSH_HOSTS` / `VISTA_MCP_LUX_ACCOUNT`.

The MCP server parses `lux_ssh_hosts` with its `CommaSeparatedList`
(`vista_mcp_server/lib/types.py`). pydantic-settings would otherwise expect JSON
for a list, so the backend field uses `NoDecode` plus a before-validator that
splits on commas. That way the same env value means the same hosts in both
services.

`test_hpc_config_parity.py` already compares every `HpcClusterSettings` default
with the MCP's literal default, so the new fields are covered automatically.

### D8. UI
- **`lib/hpc-status.ts`:** add `"lux"` to `HpcCluster`, `HPC_CLUSTERS` and
  `HPC_CLUSTER_TITLES`, and `host?: string | null` to `HpcCheck`.
- **`HpcStatusSection.tsx`:**
  - `SUBTITLES.lux = "OLCF · Slurm over SSH"`, `SHORT.lux = "Lx"`.
  - `credentialRow` gets Lux's title.
  - `globus: null` already hides the Globus row, as for Perlmutter.
- **`UserSettingsModal.tsx`:**
  - `CREDENTIAL_FIELDS.lux = []`.
  - The Lux section holds only the show/hide switch: no inputs, nothing to
    save, and no recheck on save.

Read `ui/node_modules/next/dist/docs/` before touching the components, per
AGENTS.md.

### D9. Tests
- **Unit (`backend/tests/test_hpc_status.py`)** with a fake `lux_probe`:
  - greeting → Ready;
  - each failure → Couldn't verify with the message;
  - two users within the TTL → one probe;
  - `fresh` → re-probe;
  - hidden → no probe;
  - no token fields are consulted.
- **Real socket (same file, hermetic):** a local `asyncio` server bound to
  `127.0.0.1` that sends `SSH-2.0-test` and records what it receives (checks
  that the probe's identification line arrives). A second server sends
  `HTTP/1.1 400`, and a third accepts and never writes, which exercises the
  timeout with a shortened value.
- **Settings (`test_hpc_config_parity.py`):** parity covers the new fields. Add
  one case that `VISTA_MCP_LUX_SSH_HOSTS=a,b` parses to `["a", "b"]`.
- **Schema (`test_user_tokens.py`):** `"lux"` becomes a valid hidden cluster.
  The unknown-cluster test switches to another name.
- **Live (`backend/tests/live/test_hpc_status_live.py`, `live` marker, out of PR
  CI):** probe the real hub.
- **UI (`ui/tests/…`, `ui/e2e-hermetic/fixtures.ts`):** the `HPC_STATUS` fixture
  gains a Lux entry, and the rail, popover and modal tests cover it.

## Risks / Trade-offs

- **The hub answering doesn't mean Lux works.** The login node, Slurm or Lux
  itself can be down behind a green card. → The facility row names the hub it
  reached, and `lux-hello` (D0) is the end-to-end check.
- **A hosted deployment off the ORNL network shows Lux grey forever.** → This
  is what D3 intends, and "Couldn't verify" is honest. The deployment can hide
  Lux.
- **Repeated unauthenticated connections could concern OLCF security.** →
  There's at most one a minute per backend, and the probe names itself (D2).
  Tell OLCF if they ask.
- **Lux appears for every researcher, though only `stf218` members can use
  it.** → The card and settings name the project, and anyone can hide it. This
  was chosen over a default-hidden mechanism.

## Migration Plan

- **No data migration.** `hpc_hidden_clusters` is a JSON list and widening the
  literal accepts `"lux"`.
- **Order:** archive `hpc-cards` before archiving this change, because the
  deltas here modify requirements that `hpc-cards` adds.
- **Rollback:** revert the change. Stored `"lux"` entries in
  `hpc_hidden_clusters` are harmless, because reads accept any string.
