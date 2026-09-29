## 0. `lux-hello` sample job

- [x] 0.1 Add `hpc_jobs/lux-hello/` with `README.md` (starting `# lux-hello`: what it does, that it costs one node for seconds, how to run it from a chat), `cluster_defaults.json` with only a `lux` section (`node_count` 1, `duration` 300, `exclusive_node_use: false`, since it defaults to true), and `job.lux.slurm` that writes `hostname`, `date`, and `rocm-smi` output (tolerating its absence) to `$VISTA_OUT/hello.txt` and echoes them to the log (design D0); verify `cd mcp_servers/vista_mcp_server && uv run --extra dev pytest tests/test_job_catalog.py` passes
- [x] 0.2 Add a hermetic case to `mcp_servers/vista_mcp_server/tests/test_lux_submit.py` that submits `lux-hello` against the fake SSH and asserts a 1-node, 5-minute `#SBATCH` header with no `--exclusive`, no GPU directive, and no login-node setup step; verify with `uv run --extra dev pytest tests/test_lux_submit.py`
- [ ] 0.3 Manual, out of PR CI: from a chat on the dev stack, submit `lux-hello` on Lux, sign in through the hub, then fetch its status and outputs; verify `hello.txt` names a Lux compute node. If `sbatch` refuses a job without a GPU request, add `"gpus_per_node": 1` to its `resources`, rerun, and note it in the README

## 1. Backend settings and cluster list

- [ ] 1.1 Add `lux_ssh_hosts` (comma-separated, via `NoDecode` + before-validator) and `lux_account` to `HpcClusterSettings` in `backend/src/vista_backend/config.py`, with defaults copied from the MCP's `config.py`; verify `backend/tests/test_hpc_config_parity.py` passes and a new case there shows `VISTA_MCP_LUX_SSH_HOSTS=a,b` parses to `["a", "b"]`
- [ ] 1.2 Add `"lux"` to `HpcCluster` in `backend/src/vista_backend/db/schemas.py`; change `test_unknown_cluster_is_rejected` in `backend/tests/test_user_tokens.py` to reject some other name and add a case that `["lux"]` saves; verify with `cd backend && uv run --extra dev pytest tests/test_user_tokens.py`

## 2. Backend Lux checks

- [ ] 2.1 Add `host: str | None` to `Check` and generalise the `project` docstring (design D4, D6); verify the existing `tests/test_hpc_status.py` still passes
- [ ] 2.2 Implement `probe_ssh_greeting` (design D2: connect, read one line of at most 255 bytes within the timeout, require `SSH-`, send `SSH-2.0-VISTA_status_probe\r\n`, close) and map every failure to `reason="unreachable"` with a message naming host and cause (D3); verify with hermetic real-socket tests in `tests/test_hpc_status.py` against local `127.0.0.1` servers covering greeting, non-SSH first line, silent server (short timeout), and refused port, plus an assertion that the probe's identification line arrives
- [ ] 2.3 Wire Lux into `HpcStatusService`: `CLUSTERS`/`_TITLES` (Lux last), injectable `lux_probe`, per-host probe cache shared across users for `FACILITY_TTL` and bypassed by `fresh` (D5), constant `_credential_fingerprint`, constant-ok credential check with `project=lux_account` (D4), `globus=None`; verify with fake-probe tests in `tests/test_hpc_status.py`: hub answers → `ready`; hub fails → `unverifiable` (not `degraded`); two users within TTL → one probe; `fresh` → re-probe; Lux hidden → no probe; `GET /users/me/hpc-status` includes Lux and no token values
- [ ] 2.4 Add a Lux case to `backend/tests/live/test_hpc_status_live.py` that probes the real hub (marked `live`, out of PR CI); verify it passes locally with `VISTA_RUN_LIVE=1` on the ORNL network
- [ ] 2.5 Run `./scripts/ci-local.sh backend` and verify lint and tests pass

## 3. UI

- [ ] 3.1 Read the relevant docs in `ui/node_modules/next/dist/docs/` for the client components being changed (per AGENTS.md)
- [ ] 3.2 Add `"lux"` to `HpcCluster`, `HPC_CLUSTERS`, `HPC_CLUSTER_TITLES` and `host` to `HpcCheck` in `ui/lib/hpc-status.ts`; verify `ui/tests/hpc-status.test.ts` passes with a Lux entry in its fixture
- [ ] 3.3 In `ui/components/HpcStatusSection.tsx` add the Lux subtitle ("OLCF · Slurm over SSH"), short label "Lx", the Lux credential-row title, and the "only the hub is checked" note in the details (D8); verify in `ui/tests/HpcStatusSection.test.tsx` that a Ready Lux card shows green "Ready", its details show hub, project, sign-in note and the login-node note with no Globus row or expiry, and the collapsed label reads "Lux: Ready"
- [ ] 3.4 In `ui/components/UserSettingsModal.tsx` add `CREDENTIAL_FIELDS.lux = []` and an info-only Lux section (switch, sign-in note, hub and project from status when present); verify in `ui/tests/UserSettingsModal.test.tsx` the shown case, the hidden case (no hub/project), deep-linking from the Lux card expands only Lux, and toggling Lux's switch saves `hpc_hidden_clusters`
- [ ] 3.5 Add a Lux entry to `HPC_STATUS` in `ui/e2e-hermetic/fixtures.ts` and cover it in `ui/e2e-hermetic/shell.spec.ts`; verify with `./scripts/ci-local.sh ui`

## 4. End-to-end check

- [ ] 4.1 With the dev stack running from this worktree (`./launch.sh logs`), open the rail and verify Lux shows Ready, its popover and settings section match the spec, hiding it removes the card, and Recheck re-probes (hub connection visible in `logs/backend.log` or by a fresh `checked_at`)
- [ ] 4.2 Run `openspec validate lux-hpc-card --strict` and verify it passes
