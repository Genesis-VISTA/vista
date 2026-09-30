# Tasks

All tests below are hermetic (PR CI) unless marked otherwise. Run MCP tests with
`cd mcp_servers/vista_mcp_server && uv run --extra dev pytest`, backend tests with
`cd backend && uv run --extra dev pytest`, and UI tests with `cd ui && npm test`.

## 1. Credentials: any project, own Globus only (MCP server)

- [ ] 1.1 In `lib/olcf_token.py`, delete `require_s3m_project` and keep `get_s3m_token_project`. In `submit_job_mcp.py`, replace `_require_olcf_access` with `_olcf_project(cfg, cluster) -> (token, project)`, call it at submit time only, and use `project` as the Odo and Frontier JobSpec `account`. Remove the introspection calls from status, outputs and cancel. Rewrite `tests/test_olcf_token.py` and `tests/test_job_account.py` to assert that a token for an unconfigured project (e.g. `abc123`) is submitted with account `abc123`, and that introspection errors or a missing project claim fail before any Globus call.
- [ ] 1.2 In `lib/user_config.py`, reduce `require_globus_token` to the two user sources. Delete `AppSettings.{odo,frontier}_globus_refresh_token`, `..._globus_https_refresh_token`, `globus_tokens()`, `odo_account`, `frontier_account` and `odo_introspect_url`/`frontier_introspect_url` if they become unused. Update `tests/test_globus_token_resolution.py` and `tests/test_globus_token_resolution_via_tool.py`, including a case where the env vars are set, the user has no tokens, and submission fails with "connect Globus".
- [ ] 1.3 Remove `--save-env` from `scripts/get_globus_token.py`. Remove the four Globus variables from `.env.sample`, `scripts/package_launcher.sh`, `scripts/package_launcher.ps1` and `scripts/smoke_test_package.sh`. Rewrite the Globus paragraphs in README.md (env var table) and AGENTS.md so they describe only researcher-supplied Globus. Verify with `grep -rn "GLOBUS_REFRESH_TOKEN\|GLOBUS_HTTPS_REFRESH_TOKEN"`, which should return nothing outside `openspec/changes/archive`.

## 2. Remote folders, one layout, no registry (MCP server)

- [ ] 2.1 Add `odo_remote_dir`, `frontier_remote_dir` and `lux_remote_dir` to `UserConfig`, plus `require_remote_dir(cluster)`, which also covers `nersc_remote_dir` and names the setting to fill in. Delete `odo_remote_dir`, `frontier_remote_dir`, `lux_remote_dir`, `lux_account` and `session_id` from `AppSettings`, and `account`/`remote_dir` from `IriDefaults` (`lib/iri.py`). Pass `cfg` to `_submit_lux_job`. Verify with a test per cluster in `tests/test_submit_job_spec.py` and `tests/test_lux_submit.py`: an unset folder fails naming the setting.
- [ ] 2.2 Add one path helper (design decision 4) and use it in all four submit functions. The IRI `directory` / Lux `workdir` becomes `<remote_dir>`. Make the setup prefix `mkdir -p -m 2775 "$VISTA_OUT"` on every cluster. Remove the Frontier `operation_mkdir_p(out_dir)` and the Perlmutter/Lux `out/` mkdirs. Verify `tests/test_submit_job_spec.py`, `tests/test_lux_submit.py` and `tests/test_forge_pretrain_job.py` assert `<r>/<job>/src`, `<r>/out/log-%j.out`/`.err`, `VISTA_OUT=<r>/out/$SLURM_JOB_ID`, and no Globus mkdir of `out/`.
- [ ] 2.3 Replace `_require_odo_out_dir` with `_require_group_writable` for Odo and Frontier (design decision 6). Add tests for: missing folder (gives the `mkdir -p -m 2775` command), `0755` (gives the `chmod 2775` command), `2775` (proceeds), parent listing fails with 403 (proceeds), and `GlobusSessionExpired` (raises).
- [ ] 2.4 Delete `SubmittedJob`, `_submitted_jobs`, the registry load/persist helpers, `_submitted_account` and `list_hpc_jobs`. Status, outputs and cancel derive paths from the job id plus `require_remote_dir`, `_resolve_cluster` drops `job_id`, and `dry_run.is_dry_job` is checked before cluster resolution. Delete `tests/test_job_registry.py`. Update `tests/test_log_tail.py`, `tests/test_job_log_visibility.py` and `tests/test_dry_run.py`, adding a case where status works in a fresh process for a job it never submitted.
- [ ] 2.5 Make `slurm_ssh.render_batch_script` accept `account=None` and omit `#SBATCH --account`. Lux submission passes `None`. Verify in `tests/test_lux_submit.py`.
- [ ] 2.6 Update comments and docstrings in `submit_job_mcp.py` that name `chm243_auser`/`gen150_auser` or the removed registry/session folder, so they say "the project's IRI automation user". Update the Frontier/Odo setup notes in README.md: the remote folder is chosen per user and must be group-writable, with the one-time `mkdir -p -m 2775` command. Verify `grep -n "chm243\|gen150\|session_id" mcp_servers/vista_mcp_server/src` matches only job-specific or metrics code.

## 3. Backend

- [ ] 3.1 In `db/schemas.py`, add nullable `odo_remote_dir` and `lux_remote_dir` to `UserTable`. Add all three OLCF/Lux remote dirs to `_USER_CONFIG_NULLABLE_FIELDS` and to the create, update, self-update and public-with-config schemas. Remove `remote_hpc_jobs_dir` and `frontier_account` from every API schema, keeping the table columns. Verify `tests/test_user_tokens.py` round-trips the new fields, stores `""` as null, and that an existing database gains the columns on startup (`_add_missing_columns`).
- [ ] 3.2 In `config.py` `HpcClusterSettings`, delete `odo_account`, `frontier_account`, `lux_account` and the Globus refresh-token fields. Update `tests/test_hpc_config_parity.py` and `tests/test_settings_defaults.py`.
- [ ] 3.3 In `services/hpc_status.py`, remove the `wrong_project` reason, `expected_project`, `Check.identity` and the deployment Globus source, and always report the token's project. Stop reporting a project for Lux. Update `tests/test_hpc_status.py` for the modified `hpc-availability` scenarios (token for any project passes and reports it; deployment env vars do not count; Lux names no project). Update `tests/live/test_hpc_status_live.py` to match; it is `live` and stays out of PR CI.
- [ ] 3.4 Remove `list_hpc_jobs` from `HPC_TOOLS` in `agents/agents.py`, from `db/system_prompts/molten-salt.md` and from `docs/project-onboarding.md`. Update `tests/test_project_agent_tools.py`, and check in `tests/test_seed_projects.py` that a stored `!list_hpc_jobs` exclusion is harmless. Update `test_debate_simulation.py`'s wrong-project case to an error that still exists (e.g. an expired token), and `tests/harness/factories.py`.
- [ ] 3.5 Update `db/skills/llm-pretraining/SKILL.md` so it no longer tells researchers they need a chm243 token, and instead points to the remote directory setting. Verify by reading it.

## 4. UI

- [ ] 4.1 Read the relevant doc in `ui/node_modules/next/dist/docs/` first. Add Odo, Frontier and Lux remote directory fields to `components/UserSettingsModal.tsx` and `lib/user.ts`, with the group-writable hint on Odo/Frontier. Change the S3M hints to "any OLCF project with S3M access". Verify `tests/UserSettingsModal.test.tsx` covers saving, clearing and the Lux section.
- [ ] 4.2 In `components/HpcStatusSection.tsx`, remove the `wrong_project` label, tone and details, and the deployment identity text. Show the token's project for Odo/Frontier and none for Lux. Remove `list_hpc_jobs` from `lib/tool-labels.ts`. Update `tests/HpcStatusSection.test.tsx`, `tests/hpc-status.test.ts` and `e2e-hermetic/fixtures.ts` to use a project name no deployment is configured for.

## 5. Example job

- [ ] 5.1 Change `hpc_jobs/example/job.odo.slurm` and `job.frontier.slurm` to create the venv at `"$VISTA_OUT/.venv"` and write nothing else outside `$VISTA_OUT`. Verify `tests/test_job_catalog.py` still passes and a new catalog test asserts that the example scripts do not create `.venv` in the working directory.

## 6. Integration

- [ ] 6.1 Run `./scripts/ci-local.sh` (lint + test for backend, UI, MCP and electron) and `openspec validate hpc-any-project --strict`. Both pass.
- [ ] 6.2 **Manual, `hpc` — not in PR CI.** With Odo and Frontier S3M tokens from projects other than gen150-vista and chm243, the researcher's own Globus connections, and a group-writable remote folder, submit `example` to Frontier and to Odo. Restart the MCP server, then fetch status and `chart.png` by job id and cluster. Record the result in this task.
