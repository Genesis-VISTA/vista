## Context

VISTA runs four cooperating processes started by `scripts/launch.sh`: the
Next.js UI (`:3000`), the FastAPI backend (`:8001`), `vista_mcp_server`
(`:8000`), and a Globus Connect Personal endpoint. A fifth, `dev_mcp_server`,
is spawned per pooled `ProjectAgent` over **stdio** by the backend and owns the
code-execution sandbox.

```
Browser
  │  Globus OIDC session
  ▼
Kong Gateway (if-kong)
  │
  ▼ :3000
┌──────────────────── vista pod ─────────────────────┐
│  UI :3000 ──http──▶ backend :8001 ──http──▶ MCP :8000
│                        │                      ▲
│                        │ stdio        VISTA_MCP_URL
│                        ▼               (already a seam)
│                  dev_mcp_server
│                        │
│                        ▼
│                  microVM (/dev/kvm)
└─────────── all share /data (RWO PVC) ──────────────┘
```

Investigation of `main` established three facts that drive this design:

1. **Prod auth is unimplemented.** `services/auth.py` raises 501 when
   `settings.env == "prod"`; in dev, a missing `X-Vista-User-Email` header
   resolves to the seeded **admin** user. The UI BFF forwards `x-amzn-oidc-*`
   headers that the backend never reads.
2. **microsandbox needs the KVM device, not privilege.** `aws/Dockerfile.server`
   documents `--device /dev/kvm` plus kvm group membership. No `--privileged`.
3. **Sandbox concurrency is unbounded relative to pod memory.** The agent pool
   admits 50 agents (30-min TTL); each sandbox defaults to 1 vCPU / 1024 MiB.

## Goals / Non-Goals

**Goals:**

- A chart that ArgoCD can sync to a healthy pod behind Kong
- Least-privilege sandbox: KVM device access, no privileged pod
- Deterministic sandbox image availability without registry egress
- Resource limits consistent with configured sandbox concurrency
- An explicit, documented interim identity posture with a hard prod gate
- Preserve the `VISTA_MCP_URL` seam so a later service split is not a rewrite

**Non-Goals:**

- SSO implementation, JIT user provisioning, per-user authorization
- Multi-replica, Postgres, or HA
- Splitting services now
- Autoscaling, DR, backup policy

## Decisions

1. **Single pod, `replicas: 1`, `Recreate` strategy.**
   Forced, not chosen: SQLite plus a ReadWriteOnce PVC allows one writer, and
   the agent pool is process-local (`services/project_agent.py` carries a TODO
   acknowledging this). Rolling updates would put two writers on one volume.

2. **KVM via device access; `privileged: true` removed.**
   Ask the platform team for a KVM-capable node pool and device exposure.
   Rationale: microsandbox only needs `/dev/kvm`; requesting blanket privilege
   invites rejection on a shared cluster and grants far more than needed.

3. **`sandboxMode` and `kvm.enabled` stay configurable, and the pod MUST start
   without KVM.**
   If KVM is unavailable, the deployment degrades to sandbox-disabled rather
   than crash-looping. Sandbox tools then fail with a clear error. Rationale:
   the first milestone is a healthy ArgoCD sync; sandbox availability must not
   gate proving the rest of the stack.

4. **Load the sandbox image from the baked tar at startup.**
   `VISTA_DEV_MCP_OCI_IMAGE_TAR` is set in `aws/Dockerfile.server` but read
   nowhere in `dev_mcp_server`. Wire it so startup runs the equivalent of
   `msb load -i`. Rationale: today the first spawn falls back to `msb pull`,
   which needs registry egress and makes cold start fragile.

5. **`MSB_HOME=/data/msb` on the PVC, kept shallow.**
   The Dockerfile notes deep nesting triggers "agent relay socket path is too
   long". Keep the path short and document the constraint.

6. **Bound the agent pool to fit the memory limit.**
   Either lower `max_size` or raise limits so `max_size × sandbox memory` fits.
   Rationale: 50 × 1 GiB against an 8 Gi limit is an OOM waiting to happen.

7. **Identity is interim and hard-gated.**
   Ship with `VISTA_ENV=dev` plus Kong-injected identity, and require that the
   backend is unreachable except through the gateway, which MUST strip
   client-supplied `X-Vista-User-Email`. The chart MUST refuse to render a
   configuration claiming multi-user readiness before SSO lands.

8. **Do not rebase `feat/helm`.**
   Redraw from `main`, using the branch's chart and README as reference for the
   AmSC platform contract (ECR paths, sync waves, ESO, HTTPRoutes).

## Risks / Trade-offs

- [**Interim identity is a real security exposure**] → Restrict to a trusted
  user set; ensure Kong strips inbound identity headers; treat SSO as blocking
  before broader access. Document loudly in the chart README.
- [Platform denies KVM device access] → Fall back to sandbox-disabled deploy
  (decision 3) while the docker-mode path is evaluated; note that socket-mount
  approaches often reclaim the privilege we saved.
- [Single pod means downtime on every deploy] → Accepted for beta; `Recreate`
  is required by RWO. Revisit with Postgres + shared storage.
- [Cold start is slow — HF model download, Chroma load, four processes] →
  Generous startup probe; consider baking or pre-seeding the model cache.
- [Two chat sessions, same user+project → two sandboxes, one shared volume dir]
  → Pre-existing race, not caused by EKS; document and file separately.
- [Globus Connect Personal in-pod headless setup] → Needs `GLOBUS_SETUP_KEY`;
  state under `/data/globusonline` must persist across restarts.

## Migration Plan

Sequence: chart renders and lints locally → image + chart published to ECR →
platform prerequisites (ECR repos, KVM node pool, ArgoCD Application, Kong
route) → first ArgoCD sync with sandbox disabled → enable KVM and verify
sandbox → document interim identity posture and access restrictions.

Rollback is `Recreate` back to the prior chart version; `/data` persists
independently of the pod.

## Open Questions

- Do AmSC node types expose `/dev/kvm`, and via device plugin or `securityContext`?
- Which ECR account/repo paths and ArgoCD sync wave apply now?
- Is ElastiCache Redis available for Kong OIDC session storage?
- What exact header will Kong inject, and who owns stripping spoofed ones?
- Does the UI BFF's ALB-shaped `x-amzn-oidc-*` forwarding survive, or is it
  replaced by the Kong equivalent?
