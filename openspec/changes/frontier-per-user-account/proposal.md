## Why

VISTA refuses Frontier to anyone outside one hardcoded OLCF project.
`settings.frontier_account` is `"chm243"`, and `_require_olcf_access` rejects any
S3M token whose `project` claim differs. A researcher in another project cannot
use Frontier through VISTA at all — not with their own allocation, not at all.

The fix is smaller than it looks. An S3M token is **group-scoped: it carries
exactly one `project` claim**, and `get_s3m_token_project()` already reads it.
So VISTA does not need the user to name their project, and does not need a new
settings field. It needs to stop asserting the project equals a constant and
start *using* the one the facility already put in the token.

## What Changes

- **The Slurm account becomes the token's project.** `frontier_account` stops
  being a requirement and becomes only *the deployment's own* project — what
  decides whether a user is on the pre-provisioned path.
- **A job may still pin an account.** `cluster_defaults.json` `account` wins where
  present, and a token for a different project is refused, as today.
- **The remote dir follows the account.** New `frontier_remote_dir_template`
  (`/lustre/orion/{account}/proj-shared/vista`); the existing
  `frontier_remote_dir` still serves the deployment project, so nothing changes
  for current users.
- **Off the deployment project, no shared Globus identity.** Today, `token.project
  == frontier_account` is what authorizes falling back to the deployment's Globus
  credential. Once any project's token is accepted, that fallback would let VISTA
  act as the deployment-mapped identity for an outsider. Such users must connect
  their own Globus; the facility then enforces what they may touch.
- **A first-run permissions preflight**, mirroring Odo's: name the exact
  `mkdir -p -m 2775` to run when a project's VISTA dir is missing or not
  group-writable.
- The **effective** account is recorded on the submitted job, so status and output
  fetches re-verify against the same project after a restart.

## Capabilities

### Modified Capabilities

- `hpc-job-contracts`: Frontier's OLCF project becomes per-user, derived from the
  caller's token, with the Globus-identity and directory-permission consequences
  that follow.

## Non-goals

- **Odo is untouched.** Its account is a VISTA-specific project and its
  permissions model assumes a single shared identity; changing it is a separate
  decision with a different threat model.
- No change to Perlmutter or Lux.
- No new user-facing settings field — deriving from the token is the point.
- VISTA still cannot grant project membership. A user with no OLCF project still
  cannot run; that is an allocation question, not a code one.

## Impact

- `mcp_servers/vista_mcp_server/src/vista_mcp_server/{config.py, submit_job_mcp.py}`,
  `lib/user_config.py`
- `mcp_servers/vista_mcp_server/tests/test_submit_job_spec.py` and a new
  per-user-account test module
- README: Frontier onboarding for a project that has never run VISTA
