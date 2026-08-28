## 1. Confirm platform reality

- [ ] 1.1 Confirm AmSC EKS cluster access, target environment, and namespace
- [ ] 1.2 Confirm whether node types expose `/dev/kvm`, and how it is granted
      (device plugin vs `securityContext`) — see design Open Questions
- [ ] 1.3 Confirm ECR account and repository paths for image and chart
- [ ] 1.4 Confirm Kong Gateway name/namespace, hostname, and whether Redis is
      available for OIDC session storage
- [ ] 1.5 Confirm the exact identity header Kong injects and who strips spoofed ones
- [ ] 1.6 Record answers in `design.md`, resolving the Open Questions section

## 2. Fix runtime gaps found on `main`

- [ ] 2.1 Wire `VISTA_DEV_MCP_OCI_IMAGE_TAR` in `mcp_servers/dev_mcp_server/`
      so the baked sandbox tar is loaded when the image is absent
- [ ] 2.2 Emit an explicit error when the configured tar path is missing; no
      silent registry-pull fallback
- [ ] 2.3 Add hermetic unit coverage for tar-load selection and the
      missing-path error (mock the runtime; do NOT require a live sandbox)
- [ ] 2.4 Make the pod start cleanly when the sandbox is unavailable — sandbox
      tools fail with an actionable error, no crash-loop
- [ ] 2.5 Reconcile agent pool `max_size` (`backend/src/vista_backend/services/project_agent.py`)
      with per-sandbox memory and the chart memory limit
- [ ] 2.6 Verify `MSB_HOME` under the data volume stays shallow enough for the
      socket path length limit noted in `aws/Dockerfile.server`

## 3. Author the chart

- [ ] 3.1 Create `chart/` (Chart.yaml, values.yaml, templates, README) drawn
      from current `main`; use `feat/helm` (`57e8a7f`) as reference only
- [ ] 3.2 Deployment: `replicas: 1`, `Recreate`, no `privileged: true`,
      KVM device access gated by a values flag
- [ ] 3.3 PVC for `/data`; document what persists (database, knowledge base
      stores, per-user volumes, Globus config, sandbox runtime home)
- [ ] 3.4 ConfigMap for non-sensitive settings; Secret with empty defaults plus
      an external-secret path
- [ ] 3.5 Service and gateway routes, including any unauthenticated static-asset
      path required before a session exists
- [ ] 3.6 Startup/liveness/readiness probes sized for cold start
- [ ] 3.7 Resources consistent with task 2.5; document the relationship
- [ ] 3.8 Keep `VISTA_MCP_URL` configurable and overridable off-pod

## 4. Identity posture (interim — SSO is a separate change)

- [ ] 4.1 Document in the chart README that prod mode returns 501 and dev mode
      resolves header-less callers to the seeded admin
- [ ] 4.2 Ensure the gateway strips or overwrites client-supplied
      `X-Vista-User-Email`; verify the backend is not reachable bypassing it
- [ ] 4.3 Restrict access to a documented trusted user set while interim
- [ ] 4.4 Document shared-identity consequences (projects, chat history, stored
      HPC credentials, sandbox files)
- [ ] 4.5 File the follow-up SSO change and link it from `proposal.md` as blocking

## 5. CI publishing

- [ ] 5.1 Add chart lint + `helm template` to merge request CI and
      `./scripts/ci-local.sh`
- [ ] 5.2 Build image with the GitLab token as a build secret; verify no
      credential lands in a published layer
- [ ] 5.3 Vulnerability-scan the image with a documented failure threshold
- [ ] 5.4 Publish image and chart with versions derived from the same commit
- [ ] 5.5 Ensure publication does not run on merge request pipelines
- [ ] 5.6 Wire the platform deploy-notify integration so ArgoCD picks up new versions

## 6. Platform prerequisites

- [ ] 6.1 Request ECR repositories for image and chart
- [ ] 6.2 Request the KVM-capable node pool with device access (task 1.2)
- [ ] 6.3 Request the ArgoCD Application, sync ordering, and gateway route
- [ ] 6.4 Request secret store paths and the workload identity grants to read them
- [ ] 6.5 Register the Globus OIDC application and redirect URIs for the hostname
- [ ] 6.6 Capture all of the above in the chart README as the platform contract

## 7. First deploy and acceptance

- [ ] 7.1 First ArgoCD sync with the sandbox disabled; pod reaches ready
- [ ] 7.2 Reach the UI through the gateway and complete the OIDC login flow
- [ ] 7.3 Restart the pod; confirm state persists on the volume
- [ ] 7.4 Enable KVM; confirm a sandbox spawns and `run_bash` executes
- [ ] 7.5 Confirm the first sandbox spawn required no registry pull
- [ ] 7.6 Document verification steps and rollback in the chart README
- [ ] 7.7 Confirm PR CI stays hermetic — no cluster, KVM, or live sandbox
      required to merge (`not live and not hpc and not sandbox`)
