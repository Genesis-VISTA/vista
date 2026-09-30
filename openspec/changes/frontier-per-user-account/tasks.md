## 1. Resolve the account from the token

- [ ] 1.1 Change `_require_olcf_access` to return the introspected project
      alongside the token (or a small result object), instead of only asserting
      equality — `lib/olcf_token.get_s3m_token_project` already fetches it
- [ ] 1.2 In `_submit_frontier_job`, resolve `account = defaults.account or
      <token project>`; keep the pinned-account refusal path unchanged
- [ ] 1.3 Redefine `settings.frontier_account` in its docstring as *the
      deployment's own project*, not a requirement every user must satisfy
- [ ] 1.4 Record the resolved account on `SubmittedJob` at the call site in
      `submit_hpc_job` (today it stores only `cluster_defaults.frontier.account`)

## 2. Directories

- [ ] 2.1 Add `frontier_remote_dir_template` to settings, defaulting to
      `/lustre/orion/{account}/proj-shared/vista`
- [ ] 2.2 Resolve the base dir: job `remote_dir` -> `frontier_remote_dir` when the
      account is the deployment's own -> template. Keep the literal deployment
      path for the deployment project (design decision 3)
- [ ] 2.3 Add the first-run permissions preflight, mirroring the Odo one at
      `submit_job_mcp.py` ~line 636: refuse with `mkdir -p -m 2775 <path>` when the
      base is missing, and with `chmod 2775 <path>` when it is not group-writable

## 3. Globus identity

- [ ] 3.1 Add `allow_deployment: bool = True` to
      `UserConfig.require_globus_token`; when false, skip `settings.globus_tokens`
      and refuse with a message that explains the reason is the non-default project
- [ ] 3.2 Pass `allow_deployment=(account == settings.frontier_account)` from all
      three Frontier call sites: submit, status, outputs
- [ ] 3.3 Leave every Odo call site alone (design decision 7)

## 4. Tests

- [ ] 4.1 `FakeIriClient` JobSpec tests: token project `abc123` with no pinned
      account produces `account: "abc123"` and the templated remote dir
- [ ] 4.2 Deployment-project user is unchanged — same account, same literal
      directory, same Globus fallback (the regression guard for decision 3)
- [ ] 4.3 Pinned-account job refuses a token from another project, naming both
- [ ] 4.4 Outside user with no own Globus is refused, and the message says why;
      with their own pair, it proceeds
- [ ] 4.5 Missing / non-group-writable base dir refuses before submission with the
      exact command
- [ ] 4.6 Status and outputs re-check against the job's recorded account after
      reloading the registry from disk
- [ ] 4.7 Markers: `unit`. Everything hermetic — no S3M, no Globus, no Frontier
- [ ] 4.8 Lint and test as CI does (ruff pinned in `.gitlab-ci.yml`)

## 5. Docs

- [ ] 5.1 README: onboarding a project that has never run VISTA on Frontier — mint
      an S3M token for the project, connect Globus, and the one-time
      `mkdir -p -m 2775` with the reason it cannot be automated
- [ ] 5.2 Note in the same place that Odo remains single-project by design

## 6. Archive

- [ ] 6.1 `openspec validate frontier-per-user-account --type change` passes
- [ ] 6.2 Archive and merge the delta into `openspec/specs/hpc-job-contracts`
