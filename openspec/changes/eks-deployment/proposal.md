## Why

Platform raised [vista-questions.md](https://gitlab.com/amsc2/infrastructure-and-services/infrastructure-services/container-services-platform/amsc-platform/-/blob/dev/docs/review/vista-questions.md)
(2026-09-18) before further VISTA onboarding. The hello-world nginx on
`amsc-ms-dev` is fine as a connectivity test. The architecture behind the next
artifacts was not written down, and several properties conflict with
[RFC §5E](https://gitlab.com/amsc2/infrastructure-and-services/infrastructure-services/container-services-platform/amsc-platform/-/blob/dev/docs/rfc/rfc_platform_architecture_mvp.md)
(*Generative AI Strategy & Model Access Gateway*).

An earlier draft of this change optimized for “a URL this week” (sandbox off as
a soft carve-out, unauthenticated gateway, direct model calls, optional EC2
tunnel). That draft does **not** answer platform’s questions. This revision
does: VISTA will comply with the current RFC and with the platform onboarding
contract on Model Services (`amsc-ms-dev`, account `890890990154`).

## What Changes

- Deploy the real VISTA stack **in-cluster** on `amsc-ms-dev` via Helm + ArgoCD
  (Kong → Service → pod). **No EC2.** **No nginx reverse-proxy to a VM.** The
  existing `amsc-vista-nginx` app remains a disposable smoke test.
- **Comply with RFC §5E on execution:** do not run microsandbox / arbitrary
  LLM-generated code on platform infrastructure. Local `run_bash` /
  sandbox tools stay disabled in this deploy. Document the Tool Registry /
  Wormhole path as the compliant long-term execution model; any return of
  local MicroVM execution requires an **RFC amendment**, not a quiet chart flag.
- Route inference through the **Model Access Gateway (MAG)** where available;
  treat direct provider credentials as interim debt with an explicit migration
  task — not as the steady state.
- Ship a **hardened chart**: `securityContext`, NetworkPolicy, Kong OIDC
  companion (PingAM on ms-dev), IRSA + External Secrets. No privileged pods.
- Full **OCI + GitOps** for image and chart (`vista/vista-server`,
  `charts/vista-server`), published from `amsc2/genesis/vista`.
- Settle namespace / naming with platform (`vista` today vs short-code form).

## Capabilities

### New Capabilities

- `eks-deployment`: In-cluster VISTA on `amsc-ms-dev` that answers
  `vista-questions.md`: placement, account, exposure, MAG, identity/secrets,
  supply chain, chart hardening, namespace — and §5E-compliant execution
  posture (no local code execution until the RFC says otherwise)

### Modified Capabilities

- (none)

## Impact

- New `chart/`; entrypoint / config so Globus and sandbox do not block boot
  and so sandbox tools are not offered in the deployed profile
- `.gitlab-ci.yml`: build/scan/push image + chart to `890890990154`
- Platform: ECR (image + chart), OIDC trust, ArgoCD Application, DNS, OIDC
  client redirect URIs, secret paths, IRSA — **not** KVM nodes or EC2
- **RFC owners / platform:** confirm §5E stands for VISTA; own any future
  amendment if MicroVM execution is required later
- **Blocking for multi-user:** map Kong/PingAM identity into VISTA users
  (today `VISTA_ENV=prod` is 501; `dev` is shared admin). Gateway OIDC alone
  is not enough
- Supersedes `feat/helm`, the EC2+nginx tunnel idea, and the prior
  “ASAP carve-out” draft of this change

## Non-goals

- Amending RFC §5E in this change (escalate separately if product needs it)
- Enabling microsandbox, `/dev/kvm`, or nested-virt node pools
- EC2 (or any out-of-cluster) app host and nginx→VM tunneling
- Dedicated `amsc-vista-dev` / Genesis-VISTA (`288834681766`) cluster
- Full Tool Registry / Wormhole implementation (specified as follow-up path)
- Splitting into `ai-chat` + `ai-agents` Deployments (single pod for now;
  keep `VISTA_MCP_URL` seam)
- Postgres / HA / multi-replica
- VISTAGuard policy work
