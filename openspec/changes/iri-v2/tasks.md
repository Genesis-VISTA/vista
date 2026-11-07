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

- [ ] 5.1 Add Perlmutter's `ClusterProfile` (IRI file transport, nested layout, NERSC account, constraint/shifter/GPU env as today); move source sync, `mkdir`, status log tails and output listing/download to the v2 filesystem calls; verify `tests/test_submit_job_spec.py` Perlmutter assertions and a new `tests/test_perlmutter_files.py` per the "Perlmutter files through IRI filesystem tasks" scenarios
- [ ] 5.2 Remove `_submit_perlmutter_job`, `_get_perlmutter_job_*` and `nersc_machine` discovery; `./scripts/ci-local.sh mcp` passes

## 6. Lux on IRI; SSH path removed (MCP)

- [ ] 6.1 Add `lux_s3m_token` to `UserConfig`; `require_s3m_token("lux")`; `require_globus_token("lux")` → Frontier pair; `globus.Cluster` gains `lux` with the Frontier collection; verify `tests/test_per_cluster_s3m_tokens.py` and `tests/test_globus_token_resolution.py` additions
- [ ] 6.2 Add Lux's `ClusterProfile` (partition → queue, gpus_per_node → GPUs per process, `RUN_DIR_Lux`, `job.lux.slurm`, `setup_lux.sh`); rewrite `tests/test_lux_submit.py` for IRI: spec, account from introspection, writable-out check, Globus source sync
- [ ] 6.3 Lux status/outputs/cancel through the shared OLCF paths; add Lux to `tests/test_dry_run.py`
- [ ] 6.4 Accept `lux` in `transfer_mcp.py`; verify `tests/test_transfer_tools.py` (Lux list/upload through the Frontier collection, `perlmutter` still refused)
- [ ] 6.5 Delete `lib/slurm_ssh.py`, `_lux_conn`, `_sync_job_sources_ssh`, `_get_lux_job_*` and their tests; keep `lib/ssh.py` for agenthpc; verify `rg -n "slurm_ssh|lux_ssh|_lux_conn" mcp_servers` is empty and `./scripts/ci-local.sh mcp` passes

## 7. Backend and UI

- [ ] 7.1 Add `lux_s3m_token` (`EncryptedStr`, nullable) to `local_settings`, the settings update/public models (write-only) and the MCP metadata; verify a test that a DB already holding the column starts without a schema problem, and that the token never appears in a settings dump
- [ ] 7.2 Normalize `lux` → `frontier` in Globus credential selection and the `/settings/globus/{cluster}` OAuth routes; verify connecting from Lux stores the Frontier pair and rechecks both
- [ ] 7.3 Readiness by resource id at each cluster's API version (v2 OLCF, v1 Perlmutter) in `services/hpc_status.py`; Lux facility/credential/Globus checks; drop the Lux account from the settings check; delete the SSH probe and backend `lux_ssh_hosts`; remove `odo_compute_resource_id`, `frontier_machine` and `nersc_machine` from both settings classes; verify `backend/tests/test_hpc_status*.py` per the hpc-availability delta scenarios; update the MCP-call comment in `agents/agents.py`; `./scripts/ci-local.sh backend` passes
- [ ] 7.4 UI: Lux section in `ui/components/UserSettingsModal.tsx` (S3M token, remote directory, shared Globus note, no account field), `ui/lib/settings.ts`; Lux card details in `HpcStatusSection.tsx` / `ui/lib/hpc-status.ts`; verify `ui/tests/UserSettingsModal.test.tsx`, `ui/tests/HpcStatusSection.test.tsx`, `ui/tests/hpc-status.test.ts`; `./scripts/ci-local.sh ui` passes

## 8. Drop amscrot

- [ ] 8.1 Remove `amscrot-py`, the `hpc` extra and `override-dependencies` from `mcp_servers/vista_mcp_server/pyproject.toml`; `uv lock`; delete the remaining amscrot comments; verify `uv sync --extra dev` needs no gitlab.com credential and `./scripts/ci-local.sh mcp` passes
- [ ] 8.2 Remove the private-dependency handling from `.gitlab-ci.yml`, `.github/scripts/package.sh` (and the release workflow's `AMSC_GIT_TOKEN`), `aws/Dockerfile.server`, `aws/build-image.sh`; verify `scripts/build_local_package.sh` builds with no `AMSC_GIT_TOKEN` set
- [ ] 8.3 Update README, `docs/development.md`, `docs/building-packages.md`, `docs/releasing.md`; `./scripts/ci-local.sh docs` passes

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
