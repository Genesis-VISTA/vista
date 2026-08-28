## Why

VISTA has an all-in-one container image (`aws/Dockerfile.server`) built for an
"EKS beta" but no deployment artifacts on `main`. An unmerged `feat/helm` branch
carries a full chart, but it is ~2 months behind and encodes assumptions that
investigation showed to be wrong or unbootable:

- It sets `config.vistaEnv: prod`, and in prod every backend route returns
  **501** (`backend/src/vista_backend/services/auth.py`) — SSO is unimplemented.
- It runs `privileged: true`, but microsandbox needs the **`/dev/kvm` device**,
  not a privileged pod. The over-ask makes shared-cluster approval harder.
- Its 8 Gi memory limit contradicts an agent pool that admits 50 concurrent
  sandboxes at 1 GiB each.

The AmSC EKS cluster is now available. This change redraws the deployment from
current `main` so VISTA can run on it.

## What Changes

- Add `chart/` — a Helm chart targeting the AmSC hub-and-spoke ArgoCD model
- Request **KVM device access** rather than privileged pods; make sandbox mode
  and KVM configurable so the pod can start without it
- Pre-load the sandbox OCI image at startup (wire the currently-unread
  `VISTA_DEV_MCP_OCI_IMAGE_TAR`) so first spawn needs no registry egress
- Size resources against real sandbox concurrency; bound the agent pool to fit
- Add CI to build/scan/push the image and package/push the chart to ECR
- Document the platform-team contract: ECR repos, ArgoCD Application, Kong
  Gateway route, KVM node pool, Secrets Manager paths and IRSA
- Deploy behind Kong with Globus OIDC at the edge, with an **explicitly interim,
  documented** identity posture until SSO lands

## Capabilities

### New Capabilities

- `eks-deployment`: Container image, Helm chart, GitOps integration, runtime
  requirements (storage, KVM, resources), and the platform-team contract for
  running VISTA on the AmSC EKS cluster

### Modified Capabilities

- (none)

## Impact

- New `chart/` directory; `aws/Dockerfile.server` and `aws/build-image.sh`
- `dev_mcp_server` startup: load sandbox image from tar
- `.gitlab-ci.yml`: image build/scan/push, chart lint/package/push, deploy-notify
- Coordination required with `amsc-platform` (ECR, ArgoCD, Kong, IRSA, nodes)
- **Blocking dependency:** production SSO. `VISTA_ENV=prod` returns 501 today,
  and `VISTA_ENV=dev` collapses every caller to the seeded admin user. A
  separate change MUST map Kong/Globus identity to an `app_user` before this
  deployment serves more than one real person. This change delivers the
  infrastructure and MUST NOT be treated as multi-user-ready on its own.
- Supersedes the unmerged `feat/helm` branch (commits `57e8a7f`, `af68aa1`),
  which is used as reference, not rebased.

## Non-goals

- Implementing SSO / user provisioning (separate change; named dependency above)
- Splitting UI / backend / MCP into separate Deployments (single pod for now;
  design keeps the `VISTA_MCP_URL` seam intact so it stays possible)
- Replacing SQLite with Postgres, or any multi-replica / HA topology
- VISTAGuard policy work
- Production-grade autoscaling, DR, or backup policy
