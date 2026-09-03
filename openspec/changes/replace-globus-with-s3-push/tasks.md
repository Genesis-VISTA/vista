## 1. S3 boundary + uploader

- [x] 1.1 Add `boto3` to `mcp_servers/vista_mcp_server/pyproject.toml` deps (currently only
      transitive via the optional `hpc` extra, so hermetic CI lacks it) and `uv lock`
- [x] 1.2 Add `lib/s3.py`: `S3Client` (`list_objects`, `download_file`, `get_text`,
      `delete_prefix`) wrapping sync boto3 in `asyncio.to_thread`, plus a module-level
      `create_s3_client(...)` factory, modeled on the deleted `lib/globus.py`
- [x] 1.3 Add `jobscripts/s3_put.py`: stdlib-only SigV4 uploader (walks `$VISTA_OUT`,
      uploads Slurm logs, writes `manifest.json` last, multipart above 5 GiB, honors
      `https_proxy`). Must NOT live under `hpc_jobs/` — `get_available_jobs()` raises at
      import for any dir without a job script
- [x] 1.4 Add a `jobscripts_dir` setting next to `mcp_apps_dir` / `dockerfile`

## 2. Config

- [x] 2.1 Add nested `S3Settings` (`bucket`, `region`, `prefix`, `key_id`, `secret`,
      `endpoint`) mounted as `s3`, following the `MetricsSettings` / `FaultSettings`
      pattern so env vars are `VISTA_MCP_S3__<FIELD>`. One credential serves both the
      job's push and Vista's reads; `lib/s3.py` falls back to the boto3 default chain
      when it is unset
- [x] 2.2 Add `require_job_credentials()` raising `ToolError`, in the style of the deleted
      `require_globus_token`
- [x] 2.3 Remove `odo_globus_collection_id`, `odo_globus_refresh_token`,
      `frontier_globus_collection_id`, `frontier_globus_refresh_token`,
      `vista_globus_collection_id`, `require_globus_token`. **Keep**
      `globus_native_app_client_id` — the NERSC IRI token script reads it

## 3. Submit path

- [x] 3.1 Delete `_sync_job_sources`, `_require_odo_out_dir`, `_olcf_collection_id`, both
      `vista_globus_collection_id` ToolError guards, the `lib.globus` import, and the
      Globus-latency log cache (`_LOG_CACHE_TTL_S` + landing-file block). Keep
      `_PRE_RUN_STATES`
- [x] 3.2 Give Odo the session-scoped `{base}/{session_id}/out` layout and drop
      `mkdir -m 2775`
- [x] 3.3 Extend the Odo + Frontier `setup_snippet`: export `VISTA_SCRATCH` and
      `VISTA_S3_*`, default `HOME` to scratch, materialize inlined sources into
      `RUN_DIR_<Cluster>` under scratch, write `s3_put.py`, install the upload-then-clean
      exit trap
- [x] 3.4 Export `VISTA_SCRATCH` from the Perlmutter snippet too (job scripts are shared);
      leave its IRI file path otherwise untouched
- [x] 3.5 Rewrite `_get_olcf_job_status` on `get_text` + `list_objects`, converging on the
      shape `_get_perlmutter_job_status` already has and reusing `_flatten_ls_paths`;
      report a missing manifest on a terminal state as an incomplete push
- [x] 3.6 Rewrite `_get_olcf_job_outputs` on `download_file`, preserving the
      `/mnt/data/output/<job_id>/<relpath>` contract, the `..`/absolute rejection, and the
      cheap-on-repeat behavior
- [x] 3.7 Keep `_require_olcf_access`; rewrite its Globus-framed docstring, and the same in
      `lib/olcf_token.py` and the `odo_introspect_url` setting

## 4. Job scripts (scratch contract)

- [x] 4.1 `salt-chemistry-md/job.frontier.slurm` — `HOME` and `CLONE_DIR` → `$VISTA_SCRATCH`
      (a ~10 MB git clone currently lands in `$VISTA_OUT`)
- [x] 4.2 `forge-tune/job.perlmutter.slurm` — `PYTHONUSERBASE` → `$VISTA_SCRATCH`
- [x] 4.3 `salt-neutronics-tbr/job.{odo,perlmutter}.slurm` — `$WORK` → `$VISTA_SCRATCH`;
      simplify the comment that justifies the split by Globus ls latency
- [x] 4.4 Document the `$VISTA_OUT` / `$VISTA_SCRATCH` contract in `README.md` § Job Output

## 5. Deletions and docs

- [x] 5.1 Delete `lib/globus.py`, `scripts/launch_globus.py`, `tests/fakes/globus.py`, and
      `globus-sdk` from `pyproject.toml`
- [x] 5.2 Remove the six Globus hooks from `scripts/launch.sh` (setup call, `GLOBUS_CMD`,
      tmux pane, terminal, log echo, `run_service`)
- [x] 5.3 Strip the OLCF half of `scripts/get_globus_token.py`, keeping the NERSC
      `--cluster perlmutter` flow intact
- [x] 5.4 Docs: `README.md` (prereq, two env rows, transfer sentence), `AGENTS.md`,
      `.env.sample`, `aws/.env.sample` (add `VISTA_MCP_S3__*`, note versioning + lifecycle),
      `docs/skill-onboarding.md`, `hpc_jobs/**` staged-via-Globus prose, and the stale
      pre-Globus `scp` reference in `forge-tune/setup_frontier.sh`

## 6. Tests

- [x] 6.1 `tests/fakes/s3.py` → `FakeS3Client` in house style (keyword-only ctor, recorder
      lists, seedable `objects`); export from `tests/fakes/__init__.py`
- [x] 6.2 `tests/test_submit_job_spec.py` — drop the `FakeGlobusClient` wiring; assert the
      JobSpec env carries `VISTA_S3_*` + `VISTA_SCRATCH`, that inlined sources and the trap
      appear in `job_cmd`, and that `HOME` falls back to scratch
- [x] 6.3 New `tests/test_s3_uploader.py` — SigV4 canonical requests compared against
      botocore's own `S3SigV4Auth` (a real oracle; note the generic `SigV4Auth`
      double-encodes paths and is the wrong reference for S3), signing-key scoping,
      the `$VISTA_OUT` walk and relative-key mapping, symlink skipping, multipart
      splitting and abort-on-failure, path-style endpoint addressing, and
      manifest-written-last ordering
- [x] 6.4 `ruff check` + `ruff format` clean on `tests/` (CI lints tests only)

## 7. Acceptance

Items 7.5-7.6 need a real cluster and stay out of PR CI (`hpc` marker / manual).

- [x] 7.1 `./scripts/ci-local.sh` green; `vista-mcp:test` stays a required passing job
- [x] 7.2 `openspec validate --all` passes
- [x] 7.3 `grep -ril globus` leaves only the NERSC IRI token path, the VISTAGuard semgrep
      rule, and this change's own prose
- [x] 7.4 No Globus service left in `scripts/launch.sh` (`bash -n` clean); the MCP server
      boots standalone and advertises all six job tools plus `display_file` with no errors.
      A full three-service `./launch.sh logs` run still wants a machine with the gated
      embedding model available (RAG fails to boot without an `HF_TOKEN`)
- [ ] 7.5 **Out of PR CI (`hpc` marker / manual):** `hpc_jobs/example` end-to-end on Odo —
      status QUEUED → RUNNING → COMPLETED, manifest + `chart.png` + both logs in S3,
      `get_hpc_job_outputs` → `display_file` renders the PNG. Repeat on Frontier
- [ ] 7.6 **Out of PR CI:** scratch discipline — the Odo prefix contains only
      `chart.png` + logs + manifest, the cluster scratch dir was removed, and
      `salt-chemistry-md` on Frontier does not upload its git clone
- [x] 7.7 Negative checks (both covered locally): unset `VISTA_MCP_S3__SECRET` raises
      before the job is submitted (`test_submit_odo_job_requires_push_credentials`), and a
      body that installs its own `EXIT` trap and exits non-zero still pushes its output
      while propagating its exit code (verified by executing the generated shell against a
      local fake S3 endpoint)
