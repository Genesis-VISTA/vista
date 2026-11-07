# Tasks

Each numbered group is one reviewable commit. All but group 10 run in PR CI
(`-m "not live and not hpc and not sandbox"`). Tasks marked **[live]** need real
credentials and stay out of PR CI.

## 1. Frontier never goes to Lux (v1 hotfix, can ship alone)

- [x] 1.1 Pin Frontier's v1 compute resource id in MCP `config.py` (`frontier_compute_resource_id`, `5173cdb4-d82f-5c81-8ef0-598997c12813`) and pass it from `create_olcf_iri_client`, as Odo does; verified by `tests/test_iri_resolve.py::test_frontier_client_is_pinned_to_frontier` and live (`create_olcf_iri_client` now resolves `5173cdb4-…`, was Lux's `7fd317bd-…`)
- [x] 1.2 Make `IriClient._resolve_resources` match names case-insensitively and raise instead of falling back to `compute[0]`; verify with a fake discovery listing Lux before Frontier in a new `tests/test_iri_resolve.py`
- [x] 1.3 `./scripts/ci-local.sh mcp` passes

## 2. Recorded facility responses

- [x] 2.1 **[live]** Probe S3M v2 on Lux and NERSC v1/v2 on Perlmutter (done 2026-11-06; findings in design.md Context: NERSC v2 refuses NERSC IRI tokens, v1 works end to end)
- [x] 2.2 Commit sanitized fixtures from those probes: `mcp_servers/vista_mcp_server/tests/fixtures/iri/olcf_v2/{submit,queued,failed_exit7,canceled}.json` and `tests/fixtures/iri/nersc_v1/{submit,queued,failed_exit7,task_submitted,task_pending,mkdir,upload,ls,ls_recursive,head,stat,download_text,download_binary,head_missing,ls_missing}.json` (usernames, project, paths, hosts and ids replaced; provenance in the folder's README); verify `tests/test_iri_fixtures.py` parses each and checks its shape

## 3. The IRI client

- [x] 3.1 Rewrite `lib/iri.py` as the httpx client (`IriTarget` with `api_version`, `IriClient`: compute calls, normalization and error mapping per design §1); verify `tests/test_iri_client.py` with `httpx.MockTransport`: paths and bearer for each of the four clusters at their versions, 401, 404 on submit, POST timeout not retried, 204 cancel, golden status fixtures from 2.2. The old factory names (`create_iri_client`, `create_olcf_iri_client`, `create_odo_iri_client`) now return the new client, so from this group on Odo and Frontier submit on v2 and Perlmutter on v1 without amscrot; `wire_job_spec` still lifts `directory`/`stdout_path`/... out of `attributes` and drops `resource_id` until group 4 builds the wire shape directly. `tests/test_iri_resolve.py` (group 1's discovery tests) is deleted: nothing is discovered any more, and the Frontier/Lux separation is `test_frontier_and_lux_share_an_enclave_but_never_a_resource`
- [x] 3.2 Add filesystem task calls (`mkdir`, `ls`, `head`, `stat`, `download`, `upload`, `_await_task`) for v1 and v2 argument placement; verify the same test file covers completed, failed, still-active-at-bound and 404-missing tasks, `ls` real-path rewriting, and text vs base64 download reconciled against `stat` size (and a mismatch failing)
- [x] 3.3 Status formatting tests load the 2.2 fixtures: failed exit 7 → FAILED with exit code 7; queued → QUEUED with no exit code and no message
- [x] 3.4 Config per design §2 in MCP `config.py` and backend `hpc_settings` (`perlmutter_iri_url`, also read from `VISTA_MCP_NERSC_IRI_URL`); extend `backend/tests/test_settings_defaults.py`. `odo_compute_resource_id`, `frontier_machine` and `nersc_machine` stay on both sides, read only by the backend's v1 facility check, and go with it in 7.3 (the parity test needs them on both); MCP's `odo_machine` and `frontier_compute_resource_id` are removed
- [x] 3.5 Update `tests/fakes/iri.py` `FakeIriClient` to the new interface (`ls` entries with `type`, `head` of a missing file raises `IriFileNotFound`); the old amscrot client is no longer imported anywhere (`test_nothing_imports_amscrot`) but stays installed until group 8; `tests/test_amscrot_log_location.py` is deleted with the code it tested

## 4. Odo and Frontier on v2 through one submit path

- [x] 4.1 Introduce `ClusterProfile` and `_submit_job(cluster)` for Odo and Frontier (design §3) on the new client (`client_for(cluster, token)`; `_submit_odo_job`, `_submit_frontier_job`, `_create_olcf_iri_for` and the `create_odo/olcf_iri_client` factories are gone), building the IRI wire shape directly; verify `tests/test_submit_job_spec.py` Odo/Frontier JobSpec assertions (resource in path, no `resource_id` attribute)
- [x] 4.2 Inline `setup_<cluster>.sh` into the job body after the shared prefix instead of `pre_launch` (`_job_body`: `bash -lc <setup> || exit`, see design §3); verify a spec test of the body order (prefix → setup → `set --` args → job script) and, under real bash, that a failing setup exits the job with its code
- [x] 4.3 OLCF status and cancel on the new client; verify `tests/test_log_tail.py`, `tests/test_job_account.py`, `tests/test_dry_run.py`; `./scripts/ci-local.sh mcp` passes

## 5. Perlmutter on the new client (IRI v1)

- [x] 5.1 Add Perlmutter's `ClusterProfile` (`facility="nersc"`: IRI file transport, nested layout, NERSC account; constraint/shifter/GPU env as today; setup inlined as on OLCF); move source sync (`_sync_job_sources_iri`), `mkdir`, status log heads and output listing/download to the new client's filesystem calls; status is one `_get_job_status` for every IRI cluster (state, then `_globus_job_files` or `_iri_job_files`), outputs `_get_iri_job_outputs` (names checked first, local copies kept); verify `tests/test_submit_job_spec.py` Perlmutter assertions and a new `tests/test_perlmutter_files.py` per the "Perlmutter files through IRI filesystem tasks" scenarios, against `tests/fakes/nersc.py` (`FakeNersc`, the recorded shapes over an in-memory filesystem)
- [x] 5.2 Remove `_submit_perlmutter_job`, `_get_perlmutter_job_*`, `create_iri_client` and `wire_job_spec`'s lifting of fields out of `attributes` (every caller now builds the wire shape; a stray one is refused); `nersc_machine` is no longer read by the MCP server and goes with the backend's check in 7.3; `./scripts/ci-local.sh mcp` passes

## 6. Lux on IRI; SSH path removed (MCP)

- [x] 6.1 Add `lux_s3m_token` to `UserConfig` (and drop `lux_account`/`require_lux_account`); `require_s3m_token("lux")`; `require_globus_token("lux")` → Frontier pair; `globus.Cluster` gains `lux` with the Frontier collection; verify `tests/test_per_cluster_s3m_tokens.py` and `tests/test_globus_token_resolution.py` additions
- [x] 6.2 Add Lux's `ClusterProfile` (partition → queue (`queue_is_partition`, checked before anything is contacted), gpus_per_node → GPUs per process (`_iri_resources`, which refuses a count that does not divide among the processes), `RUN_DIR_Lux`, `job.lux.slurm`, `setup_lux.sh` inside the job); rewrite `tests/test_lux_submit.py` for IRI: spec, account from introspection, writable-out check, Globus source sync; `test_skill_jobs.py`, `test_hello_skills.py` and `test_forge_pretrain_job.py` move their Lux cases to the IRI and Globus fakes
- [x] 6.3 Lux status/outputs/cancel through the shared OLCF paths; add Lux to `tests/test_dry_run.py`
- [x] 6.4 Accept `lux` in `transfer_mcp.py`; verify `tests/test_transfer_tools.py` (Lux list/upload through the Frontier collection, `perlmutter` still refused)
- [x] 6.5 Delete `lib/slurm_ssh.py`, `_lux_conn`, `_sync_job_sources_ssh`, `_get_lux_job_*`, `lux_proxy`, `tests/fakes/ssh.py` and their tests; keep `lib/ssh.py` for agenthpc; verify `rg -n "slurm_ssh|lux_ssh|_lux_conn" mcp_servers` finds only MCP `lux_ssh_hosts`, which the backend's SSH probe reads until 7.3 removes both, and `./scripts/ci-local.sh mcp` passes

## 7. Backend and UI

- [x] 7.1 Add `lux_s3m_token` (`EncryptedStr`, nullable) to `local_settings`, the settings update/public models and the MCP metadata, as the other S3M tokens are (not write-only: `GET /settings` returns them all for the form); drop `lux_account` from the models and metadata, keeping its column; add Lux to the debate cluster credentials (`agents/forum/simulation.py`); verify a test that a DB already holding the column starts without a schema problem (`test_local_settings.py`), and that the token reaches the MCP metadata and `lux_account` does not
- [x] 7.2 Normalize `lux` → `frontier` in Globus credential selection (`hpc_status.globus_owner`) and the `/settings/globus/{cluster}` OAuth routes (`globus_auth.credential_cluster`; `GlobusConnected.connected` names whose pair was stored); verify connecting from Lux stores the Frontier pair (`test_globus_connect.py`) and rechecks both (`test_connecting_frontiers_globus_rechecks_lux`, and the UI's `recheckFrontierAndLux`)
- [x] 7.3 Readiness by resource id at each cluster's API version (v2 OLCF, v1 Perlmutter) in `services/hpc_status.py` (the public v2 feed, read on 2026-11-07, lists `odo`, `frontier` and `lux` by those ids and answers `[]` for no incidents); Lux facility/credential/Globus checks; drop the Lux account from the settings check; delete the SSH probe and `lux_ssh_hosts` (backend and MCP); remove `odo_compute_resource_id`, `frontier_machine` and `nersc_machine` from both settings classes; verify `backend/tests/test_hpc_status*.py` per the hpc-availability delta scenarios; update the MCP-call comment in `agents/agents.py`; `./scripts/ci-local.sh backend` passes
- [x] 7.4 UI: Lux section in `ui/components/UserSettingsModal.tsx` (S3M token, remote directory, shared Globus note with Frontier's connect control, no account field; the modal holds Frontier's Globus state, so connecting from either section shows both connected), `ui/lib/settings.ts`; Lux card details in `HpcStatusSection.tsx` / `ui/lib/hpc-status.ts`; verify `ui/tests/UserSettingsModal.test.tsx`, `ui/tests/HpcStatusSection.test.tsx`, `ui/tests/hpc-status.test.ts`, and the hermetic e2e (`shell.spec.ts` Lux card; the generic login-prompt tests in `chat-runs.spec.ts` / `run-status.spec.ts` no longer say Lux); `./scripts/ci-local.sh ui` passes

## 8. Drop amscrot

- [x] 8.1 Remove `amscrot-py`, the `hpc` extra, `override-dependencies` and `allow-direct-references` from `mcp_servers/vista_mcp_server/pyproject.toml`; `uv lock` (no git sources left; `test_no_private_dependency` keeps it so); delete the remaining amscrot comments; verify `uv sync --frozen --extra dev` resolves with no git configuration or gitlab.com credential and `./scripts/ci-local.sh mcp` passes
- [x] 8.2 Remove the private-dependency handling from `.gitlab-ci.yml`, `.github/scripts/package.sh` (and the release workflow's `AMSC_GIT_TOKEN`), `aws/Dockerfile.server`, `aws/build-image.sh`, `scripts/build.sh` and `scripts/build_local_package.sh` (the amsc2 preflight and the `--extra hpc` sync); verified by shell syntax checks and the credential-free frozen sync of 8.1 (a full package build needs the inputs and a sandbox image; it runs in the release workflow)
- [x] 8.3 Update README, `docs/development.md` ("HPC job submission" replaces "HPC dependencies"), `docs/building-packages.md`, `docs/releasing.md` (the `AMSC_GIT_TOKEN` setup becomes how to retire it), and `docs/hpc.md`'s Perlmutter row; `./scripts/ci-local.sh docs` passes

## 9. Skills and docs

- [ ] 9.1 `hello-lux`: SKILL.md prerequisites (Lux token, Frontier/Lux Globus, remote dir), README, `cluster_defaults.json`; verify `tests/test_hello_skills.py`
- [ ] 9.2 `hpc-skill-author`: SKILL.md and templates say setup runs in the allocation; `wrap-repo/setup_lux.sh` takes a lock in `VISTA_JOB_DIR`; verify `tests/test_skill_author_wrap.py`, `tests/test_skill_author_validator.py`
- [ ] 9.3 Lux notes in bundled skills (`llm-pretraining`, `salt-neutronics-tbr`), the `hello-olcf` system prompt, `docs/hpc.md` (transport table: IRI v2 everywhere), `docs/hpc-jobs.md`, `docs/skill-onboarding.md`, AGENTS.md; `./scripts/ci-local.sh docs` passes
- [ ] 9.4 `openspec validate iri-v2 --strict` and the full `./scripts/ci-local.sh` pass

## 10. Live validation (manual; excluded from PR CI)

- [ ] 10.1 **[live]** Add `mcp_servers/vista_mcp_server/tests/live/test_iri_live.py` (`@pytest.mark.live`, `@pytest.mark.hpc`): v2 submit/status/cancel per cluster from tokens in the environment, and Perlmutter filesystem round trip
- [ ] 10.2 **[live]** `hello-lux` end to end in the VISTA window: report fetched, GPU named, no login prompt
- [ ] 10.3 **[live]** `hello-frontier` and `hello-odo` end to end (needs an Odo S3M token; none is saved today)
- [ ] 10.4 **[live]** One Perlmutter job end to end on the new client (sources up, log tail, a binary output down)
- [ ] 10.5 **[live]** One existing Lux skill job with a `setup_lux.sh` (e.g. `llm-pretraining`): setup works in the allocation through the proxy
- [ ] 10.6 After release: revoke `AMSC_GIT_TOKEN` (manual, maintainer)
