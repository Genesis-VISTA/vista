# Tasks

## 1. Job resolver

- [x] 1.1 Factor `_load_job(dir, name)` out of `get_available_jobs()` and add `dir` to `JobInfo`; verify `tests/test_job_catalog.py` still passes
- [x] 1.2 Add `resolve_job(job, skills_dir)` (built-ins first, then `<skill>/<job>` in the loaded skills, strict name check, containment check) and `list_skill_jobs(skills_dir)`; verify with a new hermetic `tests/test_skill_jobs.py`
- [x] 1.3 Route `submit_hpc_job` and the four dispatchers through the resolver, replacing every `settings.local_hpc_jobs_dir / job`; verify `grep local_hpc_jobs_dir submit_job_mcp.py` leaves only the catalog loader, and that `test_submit_job_spec.py`, `test_lux_submit.py` and `test_forge_pretrain_job.py` pass
- [x] 1.4 Say in the `submit_hpc_job` description that loaded skills may provide `<skill>/<job>` jobs documented in their SKILL.md, and list skill jobs in the unknown-job error; verify with a resolver test

## 2. Nested uploads

- [x] 2.1 Add `_job_sources(dir)` (relative paths; top-level metadata, dot-names, `__pycache__` and symlinks skipped); verify with unit tests
- [x] 2.2 Globus `_sync_job_sources`: recursive compare by relative path, `mkdir -p` per parent; verify with `tests/test_source_upload.py` additions using the fake Globus client
- [x] 2.3 SFTP `_sync_job_sources_ssh` and `slurm_ssh.list_file_sizes`/`upload_files` for nested paths; verify with `tests/fakes/ssh.py`-based tests
- [x] 2.4 Perlmutter `_sync_perlmutter_sources`: mkdir nested parents and upload relative paths; verify with a fake IRI test

## 3. Contract tests

- [x] 3.1 Parametrize the on-disk contract test over shipped skills' `hpc_jobs/` too, and add a skill fixture for valid and broken skill jobs; verify `./scripts/ci-local.sh mcp` passes
- [x] 3.2 Run the resolver against `../lux-primus-skill` copied as `primus-on-lux` into a temporary skills folder, and confirm `primus-on-lux/primus-smoke` resolves and a Lux submit against the fake SSH connection uploads its files (checked by hand: six files uploaded, `RUN_DIR_Lux` and `VISTA_JOB_DIR` as specified)

## 4. Trust badge

- [x] 4.1 Backend: report `hpc_jobs` (job folder names) for each skill in `GET /skills` and `GET /skills/{name}`; verify with a backend API test
- [x] 4.2 UI: map `hpc_jobs` to `hpcJobs` in the skills proxy and show a "Runs HPC jobs" badge on the Skills page; verify with `npm run lint` and a type check

## 5. Docs

- [x] 5.1 Add "Self-contained HPC skills" to `docs/hpc-jobs.md` (layout, job contract, flat vs nested files, `setup_lux.sh` on the login node, `KEY=VALUE` allow-list for `script_args`) and to `docs/skill-onboarding.md` (trust and badge); verify the docs link checker passes
