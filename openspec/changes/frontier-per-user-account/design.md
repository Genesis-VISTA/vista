## Context

Frontier access in VISTA is gated by one line in `_require_olcf_access`:

```python
await require_s3m_project(token, account or default_account, ...)
```

with `default_account = settings.frontier_account = "chm243"`. Everything else
follows from it — `frontier_remote_dir` is `/lustre/orion/chm243/proj-shared/vista`,
and the check is what the code says authorizes a user to fall back on the
deployment's shared Globus identity:

> for them, possession of an S3M token in the cluster's OLCF project is the only
> thing that authorizes moving files through Vista — `lib/olcf_token.py`

The enabling fact: **an S3M token names exactly one project**, and introspection
already returns it. The facility has therefore already answered "which project is
this user allowed to charge?" — VISTA is merely discarding the answer and
comparing against a constant instead.

Per-job overrides already exist (`cluster_defaults.json` → `account`,
`remote_dir`, used by Lux). Per-user overrides exist only for NERSC
(`nersc_account`, typed by hand in the settings modal). Frontier has neither.

## Goals / Non-Goals

**Goals:**

- A researcher in any OLCF project with a Frontier allocation can use VISTA,
  charged to their project, with files in their project's space
- No new thing for a user to type, and no way for account and token to disagree
- No weakening of the boundary the current check defends
- Byte-identical behavior for users already on the deployment project

**Non-Goals:**

- Odo, Perlmutter, Lux; granting project membership; a UI field

## Decisions

1. **The account is the token's `project` claim.**
   `_require_olcf_access` returns the project it introspected instead of only
   verifying it. `settings.frontier_account` is demoted from *requirement* to
   *the deployment's own project*, which is still what decides the directory and
   Globus-identity questions below.
   Rationale: it removes a class of bug rather than adding a field. A typed
   account (NERSC's model) can disagree with the token; a derived one cannot.

2. **A job-pinned account still wins, and still refuses a mismatched token.**
   Where `cluster_defaults.json` names an `account`, that is the job saying "this
   must be charged here" — unchanged. Only the *unpinned* case changes, from "must
   be the deployment project" to "whatever the token says".

3. **The remote dir follows the account.**
   Resolution order: the job's `remote_dir`, then `frontier_remote_dir` when the
   account is the deployment's own, then
   `frontier_remote_dir_template.format(account=...)`.
   Keeping the literal path for the deployment project matters: that directory has
   been one-time `chmod 2775`'d and is load-bearing (decision 5). Deriving it
   instead would work by coincidence and break the moment a deployment pointed
   `frontier_remote_dir` somewhere non-conventional.

4. **Off the deployment project, the deployment's Globus identity is not
   available.** `require_globus_token` grows an `allow_deployment` flag; the
   Frontier paths pass `account == settings.frontier_account`.
   Rationale: this is the whole security content of the change. Today the
   introspection gate is what earns the shared identity. Accept any project's
   token while keeping the fallback, and VISTA would read and write as the
   deployment-mapped POSIX user on behalf of someone outside that project. With
   the fallback closed, an outside user acts as themselves and the facility
   enforces the rest — which is what `lib/olcf_token.py` already says the design
   intends. The refusal must say *why*, since it is a new requirement for them.

5. **A first-run permissions preflight, mirroring Odo's.**
   The OLCF DTN's mkdir takes no mode, so a project's VISTA base dir must be
   `mkdir -p -m 2775` by hand once; otherwise the IRI automation user
   (`<project>_auser`) cannot traverse VISTA-created directories and **Slurm's
   prolog kills the job at startup**.
   Today that is one-time deployment setup, done long ago and invisible. Per-user
   accounts make it a **first-run event for every new project**, with a failure
   mode that looks like the job dying for no reason. So the Frontier path gains
   what Odo already has: detect a missing or non-group-writable base and name the
   exact command, before submitting.

6. **Record the effective account on the job.** `SubmittedJob.account` currently
   stores only the job's override (usually `None`), and status/outputs re-derive
   the default. With per-user accounts that would re-check the wrong project after
   a restart, so the resolved account is recorded instead.

7. **Odo unchanged.** Its account is a VISTA-specific project rather than a
   researcher's, and its docstring states the permissions model assumes one shared
   identity. Applying decision 4 there would change who can use Odo at all. A
   separate decision, deliberately not bundled.

## Risks / Trade-offs

- **The 2775 step is a human action in each new project.** Decision 5 turns the
  failure from an opaque prolog kill into an instruction, but cannot remove it —
  Globus cannot create a group-writable directory and Frontier has no `setfacl`.
  A new project's first VISTA run needs someone with a Frontier login.
- **Outside users must connect Globus before any file op**, including fetching
  their own outputs. This is the deliberate cost of decision 4.
- **Session outputs are no longer all under one project.** `list_hpc_jobs` and
  friends already key off the recorded job, so this is a reporting nuance, not a
  correctness one.
- **A user who switches projects** (new token, different project) will not see
  older jobs' files, because they live under the old project's tree. The recorded
  account keeps the *check* correct; it cannot move the bytes.
- **`frontier_remote_dir_template` assumes `/lustre/orion/<project>/proj-shared`.**
  True for OLCF projects today; a project with a non-conventional layout needs the
  job-level `remote_dir`, which already exists.

## Migration Plan

Additive for anyone on the deployment project: same account, same directory, same
Globus fallback, so their behavior is unchanged by construction — which is what
the existing JobSpec tests assert. New projects are opt-in by simply having a
token. No data migration; no shared dispatch behavior removed.

## Open Questions

- Should a user on the deployment project *also* be pushed to their own Globus
  identity over time? Out of scope here, but decision 4 makes the deployment
  fallback a narrower path than it was.
