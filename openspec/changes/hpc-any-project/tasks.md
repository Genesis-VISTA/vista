# Tasks

All tests below are hermetic (PR CI) unless marked otherwise. Run MCP tests with
`cd mcp_servers/vista_mcp_server && uv run --extra dev pytest`, backend tests with
`cd backend && uv run --extra dev pytest`, and UI tests with `cd ui && npx vitest run`.

Each group is one commit and spans the MCP server, backend and UI together: the
backend's HPC settings must match the MCP server's (`test_hpc_config_parity.py`),
so a setting cannot leave one without the other. Globus goes first, because
dropping the project check while a shared Globus login still exists would
reopen the hole the check was closing.

## 1. Only researcher-supplied Globus

- [x] 1.1 MCP: reduce `UserConfig.require_globus_token` to the researcher's cluster pair and shared pair. Delete `AppSettings.{odo,frontier}_globus_refresh_token`, `..._globus_https_refresh_token` and `globus_tokens()`. Rewrite `tests/test_globus_token_resolution.py` and `tests/test_globus_token_resolution_via_tool.py`, including the case where the old env vars are set, the user has no tokens, and the call is refused.
- [x] 1.2 Backend: delete the Globus refresh-token fields from `HpcClusterSettings`. `globus_source` drops the deployment source, and `Check.identity` is removed. Update `tests/test_hpc_status.py` (deployment env vars do not count) and `tests/test_settings_defaults.py`.
- [x] 1.3 UI: remove `identity` from `lib/hpc-status.ts` and the "deployment's shared identity" text from `components/HpcStatusSection.tsx`. Update `tests/HpcStatusSection.test.tsx` and `e2e-hermetic/fixtures.ts`.
- [x] 1.4 Remove `--save-env` from `scripts/get_globus_token.py`. Remove the four variables from `.env.sample`, `scripts/package_launcher.{sh,ps1}` and `scripts/smoke_test_package.sh`. Rewrite the Globus text in README.md and AGENTS.md. Verify with `grep -rn "GLOBUS_REFRESH_TOKEN\|GLOBUS_HTTPS_REFRESH_TOKEN"`, which should return only tests that prove the variables are ignored, and `openspec/`.

## 2. Any OLCF project

- [x] 2.1 MCP: in `lib/olcf_token.py`, delete `require_s3m_project` and keep `get_s3m_token_project`. In `submit_job_mcp.py`, replace `_require_olcf_access` with `_olcf_project(cfg, cluster) -> (token, project)`, called at submit time only, and use the project as the Odo and Frontier JobSpec `account`. Remove introspection from status, outputs and cancel, and remove `SubmittedJob.account`. Delete `odo_account`/`frontier_account`, and stop Frontier reading `IriDefaults.account`. Rewrite `tests/test_olcf_token.py` and `tests/test_job_account.py`: a token for project `abc123` is submitted with account `abc123`, introspection errors or a missing claim fail before any Globus call, and status/outputs make no introspection call.
- [x] 2.2 Backend: delete `odo_account`/`frontier_account` from `HpcClusterSettings`. In `services/hpc_status.py`, remove the `wrong_project` reason and `expected_project`, and always report the token's project. Update `tests/test_hpc_status.py`, `tests/live/test_hpc_status_live.py`, and `test_debate_simulation.py`'s wrong-project case.
- [x] 2.3 UI: remove the `wrong_project` label, tone and details from `components/HpcStatusSection.tsx` and `lib/hpc-status.ts`. Change the S3M hints in `components/UserSettingsModal.tsx` to "any OLCF project with S3M access". Update `tests/HpcStatusSection.test.tsx`, `tests/hpc-status.test.ts` and `e2e-hermetic/fixtures.ts` to use a project name no deployment is configured for.
- [x] 2.4 Update the README's S3M paragraph (a token from any project; its project is the job's account). Verify `grep -rn "wrong_project\|expected_project\|require_s3m_project"` returns only `openspec/`.

## 3. Remote folders as user settings

- [ ] 3.1 Backend: add nullable `odo_remote_dir` and `lux_remote_dir` to `UserTable` (reusing `frontier_remote_dir`). Add all three to `_USER_CONFIG_NULLABLE_FIELDS` and the create, update, self-update and public-with-config schemas. Remove `remote_hpc_jobs_dir` and `frontier_account` from every API schema, keeping the table columns. Update `tests/test_project_agent_tools.py` and `tests/harness/factories.py`, which set `frontier_account`. Verify `tests/test_user_tokens.py` round-trips the fields, stores `""` as null, and that an existing database gains the columns on startup.
- [ ] 3.2 MCP: add the three fields and `require_remote_dir(cluster)` (also covering `nersc_remote_dir`) to `UserConfig`. Delete `odo_remote_dir`, `frontier_remote_dir`, `lux_remote_dir` and `lux_account` from `AppSettings`, and `account`/`remote_dir` from `IriDefaults`. `_submit_lux_job` takes `cfg`, and `slurm_ssh.render_batch_script` omits `--account` when it is `None`. Delete `lux_account` from `HpcClusterSettings`, and the Lux credential check names no project. Verify per-cluster tests that an unset folder fails naming the setting, and that the Lux batch script has no `#SBATCH --account` (`tests/test_lux_submit.py`).
- [ ] 3.3 UI: add Odo, Frontier and Lux remote directory fields to `components/UserSettingsModal.tsx` and `lib/user.ts`, with the group-writable hint on Odo/Frontier. Remove the project from the Lux details. Verify `tests/UserSettingsModal.test.tsx` covers saving, clearing and the Lux section.

## 4. One folder layout, no session folder, no registry

- [ ] 4.1 MCP: add one path helper (design decision 4) and use it in all four submit functions. Delete `session_id`. The IRI `directory` / Lux `workdir` becomes `<remote_dir>`, and the prefix runs `mkdir -p -m 2775 "$VISTA_OUT"` on every cluster. Remove the Frontier `operation_mkdir_p(out_dir)` and the Perlmutter/Lux `out/` mkdirs. Verify `tests/test_submit_job_spec.py`, `tests/test_lux_submit.py` and `tests/test_forge_pretrain_job.py` assert the layout and that `out/` is never made through Globus.
- [ ] 4.2 MCP: replace `_require_odo_out_dir` with `_require_group_writable` for Odo and Frontier (design decision 6). Test a missing folder, `0755`, `2775`, a parent listing that fails with 403 (submission proceeds), and `GlobusSessionExpired` (raises).
- [ ] 4.3 MCP: delete `SubmittedJob`, `_submitted_jobs`, the registry helpers and `list_hpc_jobs`. Status, outputs and cancel derive paths from the job id and `require_remote_dir`, `_resolve_cluster` drops `job_id`, and `dry_run.is_dry_job` is checked before resolving a cluster. Delete `tests/test_job_registry.py`. Update `tests/test_log_tail.py`, `tests/test_job_log_visibility.py` and `tests/test_dry_run.py`, including status for a job this process never submitted.
- [ ] 4.4 Remove `list_hpc_jobs` from `HPC_TOOLS` (`agents/agents.py`), `db/system_prompts/molten-salt.md`, `ui/lib/tool-labels.ts` and `docs/project-onboarding.md`. Update `tests/test_project_agent_tools.py`, and check in `tests/test_seed_projects.py` that a stored `!list_hpc_jobs` exclusion is harmless.
- [ ] 4.5 Update comments and docstrings that name `chm243_auser`/`gen150_auser`, the registry or the session folder, so they say "the project's IRI automation user". Document the layout and the group-writable requirement in README.md. Verify `grep -n "session_id\|_submitted_jobs\|chm243_auser" mcp_servers/vista_mcp_server/src` matches only metrics code.

## 5. Example job and remaining text

- [ ] 5.1 Change `hpc_jobs/example/job.odo.slurm` and `job.frontier.slurm` to create the venv at `"$VISTA_OUT/.venv"` and write nothing else outside `$VISTA_OUT`. Add a catalog test asserting that neither script creates `.venv` in the working directory.
- [ ] 5.2 Update `backend/src/vista_backend/db/skills/llm-pretraining/SKILL.md` so it points to the remote directory setting rather than requiring chm243. Verify by reading it.

## 6. Integration

- [ ] 6.1 Run `./scripts/ci-local.sh` and `openspec validate hpc-any-project --strict`; both pass, apart from failures already present on `main` that come from the sandbox.
- [ ] 6.2 **Manual, `hpc` — not in PR CI.** With Odo and Frontier S3M tokens from projects other than gen150-vista and chm243, the researcher's own Globus connections, and a group-writable remote folder, submit `example` to Frontier and to Odo. Restart the MCP server, then fetch status and `chart.png` by job id and cluster. Record the result here.
