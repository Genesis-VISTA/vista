## Context

Platform’s [vista-questions.md](https://gitlab.com/amsc2/infrastructure-and-services/infrastructure-services/container-services-platform/amsc-platform/-/blob/dev/docs/review/vista-questions.md)
asks nine architecture questions before further onboarding. This design is the
VISTA answer. It replaces the earlier “ship fast with carve-outs” plan.

**Current facts (ms-dev, 2026-09):**

- Account `890890990154`, cluster `amsc-ms-dev`, VPC `vpc-08f792a6e56c15636`
- Live: `amsc-vista-nginx` in namespace `vista` (static hello world)
- ECR: `vista/vista-server` exists; **no image tags yet**. Chart repo
  `charts/vista-server` may still need provisioning
- No VISTA EC2 in ms-dev or Genesis-VISTA (`288834681766`)
- Edge IdP on ms-dev: **PingAM** (`identity.dev.amsc.ornl.gov`), not Globus

```
Browser (NLB allowlist only — no public app host)
  │  https://vista.ms.dev.american-science-cloud.org
  ▼
Kong (if-kong) + OIDC companion (PingAM)
  │  strips client identity headers; injects platform identity
  ▼
┌─────────────── vista pod (in-cluster) ────────────────┐
│  UI :3000 → backend :8001 → vista_mcp_server :8000    │
│                                                         │
│  Local sandbox / microsandbox: OFF (§5E)                │
│  Inference: MAG when available (interim debt otherwise) │
│  Secrets: External Secrets + IRSA                       │
│  /data RWO PVC (SQLite, KB stores)                      │
└─────────────────────────────────────────────────────────┘
```

### Answers to platform questions (1–9)

| # | Question | Decision |
|---|---|---|
| 1 | §5E vs local code execution | **Comply with §5E as written.** No arbitrary LLM-generated code on platform infra. Sandbox tools disabled in this deploy. Tool Registry / Wormhole is the compliant execution path (follow-up). MicroVM on AmSC requires an **RFC amendment** owned by RFC owners + platform, not a chart toggle. |
| 2 | EC2 vs cluster | **In-cluster.** EC2 + nginx proxy is rejected: it exits Istio SPIFFE/mTLS, default-deny NetworkPolicy, PSS/Kyverno, and does not make §5E happier (the instance is still platform infra). Nested virt / KVM is out of scope while §5E stands. |
| 3 | Account / network | **`amsc-ms-dev` only** (`890890990154`). No cross-account path to Genesis-VISTA. No peering/TGW/PrivateLink work for an app VM. |
| 4 | Public exposure | **No public app IP.** Ingress only via Kong / cluster NLB allowlist. |
| 5 | MAG | **Steady state = MAG.** Direct model endpoints are interim only, tracked as FinOps/DLP debt, with a migration task when MAG is reachable from ms-dev. |
| 6 | Identity / secrets | **IRSA + External Secrets** (in-cluster). Kong PingAM OIDC at the edge. Do not trust client `X-Vista-User-Email`. App-level mapping of gateway identity → `app_user` remains a **blocking follow-up** for multi-user; until then document shared-admin / restricted access. Aim toward Keycard validation per RFC §3B in that follow-up. |
| 7 | Patching / supply chain | **OCI image + Helm chart + ArgoCD**, same as other ms-dev apps. Nginx image is not the app. |
| 8 | Chart hardening | **Required in this change:** `securityContext` (non-root, drop caps, readOnlyRootFilesystem where feasible), NetworkPolicy, OIDC gateway companion — not a follow-up after a second soft launch. |
| 9 | Namespace | Prefer short-code form. Propose **`ms-vista`** (Model Services) and confirm with platform; today’s `vista` ns is the nginx smoke-test home and may be migrated or left for nginx only. ECR product prefix `vista/` stays (platform already accepted that). |

## Goals / Non-Goals

**Goals:**

- A platform-reviewable architecture that closes `vista-questions.md`
- One ArgoCD-synced, hardened pod behind Kong+OIDC on `amsc-ms-dev`
- §5E-compliant execution posture (no local sandbox)
- MAG-first inference story; GitOps-only delivery
- Preserve `VISTA_MCP_URL` for a later split toward `ai-chat` / `ai-agents` shapes

**Non-Goals:**

- RFC text edits in this repo
- Enabling microsandbox or EC2 execution hosts
- Implementing Tool Registry or Wormhole
- Multi-replica / Postgres / HA
- Replacing or “productionizing” the nginx hello world beyond leaving it alone

## Decisions

1. **In-cluster on `amsc-ms-dev`, single pod, `replicas: 1`, `Recreate`.**  
   SQLite + RWO PVC and a process-local agent pool force one writer. Matches
   RFC’s in-cluster placement for agentic runtime / chat, without pretending
   we already split `ai-agents` / `ai-chat`.

2. **§5E compliance: local execution off.**  
   Deployed config MUST NOT enable `microsandbox` (or other modes that run
   model-authored code on the node). Sandbox-backed tools fail closed with a
   clear error, or are omitted from the tool surface. Product docs state that
   compliant execution is Tool Registry / facility Wormhole (follow-up change).

3. **No EC2, no proxy tunnel.**  
   Kong HTTPRoute → Kubernetes Service → pod. Retire the “nginx fronts a VM”
   story. Keep `amsc-vista-nginx` until the real app is healthy, then remove.

4. **MAG for inference; interim only if blocked.**  
   Chart values prefer a MAG base URL. If MAG is not yet callable from
   ms-dev, an explicitly named interim provider config is allowed behind a
   values flag that the README marks non-compliant for FinOps/DLP, with a
   tracked migration task.

5. **Gateway auth now; app SSO next.**  
   First real deploy uses the platform OIDC companion pattern (PingAM), not
   an open HTTPRoute. Chart includes NetworkPolicy so the backend is not
   reachable except via the mesh/gateway path used by other apps.  
   `VISTA_ENV` stays `dev` until app-level SSO exists (`prod` → 501). Gateway
   login ≠ per-user VISTA identity; document shared-admin until the SSO
   change lands. Strip/ignore client-supplied `X-Vista-User-Email`.

6. **IRSA + External Secrets; no static AWS keys.**  
   Secrets under `/amsc/amsc-ms-dev/...` (exact path with platform). Pod
   identity reads them. EC2 instance profiles are irrelevant.

7. **Hardened chart defaults.**  
   Non-root user, dropped capabilities, no `privileged`, resource limits,
   NetworkPolicy, probes that do not require Globus or a sandbox. Globus
   Connect Personal and HPC submit remain off unless separately enabled and
   approved.

8. **Publish path = nginx pattern + deploy-notify optional later.**  
   CI pushes `vista/vista-server:<sha>` and `charts/vista-server:0.1.0-<sha>`
   on `main`. ArgoCD Application under `argocd/apps/amsc-ms-dev/`. Human pin
   or bot — either is fine after the first version exists.

9. **Do not rebase `feat/helm`.**  
   It assumed `VISTA_ENV=prod`, privileged pods, and sandbox sizing that
   contradict both §5E and auth reality.

## Risks / Trade-offs

- [Agents without local code execution are a smaller product] → Accepted to
  comply with §5E. Chat, RAG, and platform/HPC tools that are API-shaped can
  still land; `run_bash` does not.
- [MAG may not be on ms-dev yet] → Interim flag creates FinOps/DLP gap;
  visible in README and tasks so it cannot be silent forever.
- [Shared admin after gateway login] → Users authenticate to PingAM but VISTA
  may still collapse to one DB user until SSO mapping exists. Restrict who
  gets the hostname; treat multi-user SSO as blocking before broad access.
- [Namespace rename churn] → Migrating `vista` → `ms-vista` needs platform
  + DNS/Application updates; confirm before the first real Application YAML.
- [Single-pod Recreate downtime] → Accepted for beta.

## Migration Plan

1. Post / align on answers to `vista-questions.md` with platform + RFC owners
   (§5E stands; in-cluster; no EC2).
2. Platform: chart ECR if missing, ArgoCD Application, DNS, OIDC redirect
   URIs, secret paths, IRSA.
3. Image boots without Globus; sandbox off; chart hardens and lints.
4. Publish image+chart; sync; verify OIDC → UI; verify sandbox tools unavailable.
5. Remove or leave nginx smoke test per platform preference.
6. Follow-ups (separate changes): app-level SSO / Keycard, MAG cutover,
   Tool Registry / Wormhole, optional §5E amendment only if product requires
   local MicroVMs.

## Open Questions

- Exact MAG URL / auth from `amsc-ms-dev` (or confirm interim for first sync).
- Final namespace: keep `vista` vs `ms-vista` (platform).
- Hostname: `vista.ms.dev.american-science-cloud.org` vs reuse nginx host.
- Secret path naming: `/amsc/amsc-ms-dev/<app>` convention confirmation.
- Whether deploy-notify bot is required for first pin or human MR is enough.
