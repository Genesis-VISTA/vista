## 1. Align with platform on vista-questions

- [ ] 1.1 Deliver the written answers (design table) to platform / Vanessa /
      RFC owners: §5E stands; in-cluster; no EC2; MAG-first; IRSA/ESO;
      hardened OIDC chart
- [ ] 1.2 Confirm namespace (`vista` vs `ms-vista`) and hostname with platform
- [ ] 1.3 Confirm MAG reachability from `amsc-ms-dev` or accept a named interim
      inference flag for first sync only
- [ ] 1.4 Request platform prerequisites: `charts/vista-server` if missing,
      ArgoCD Application, DNS, PingAM redirect URIs, secret path, IRSA —
      **not** KVM nodes or EC2

## 2. Runtime posture (§5E + boot)

- [ ] 2.1 Skip Globus Connect Personal unless explicitly enabled so missing
      `GLOBUS_SETUP_KEY` does not exit the container
- [ ] 2.2 Disable local sandbox / microsandbox in the deployed profile; tools
      that execute model-authored code fail closed with a clear error
- [ ] 2.3 Do not bake or require `vista-sandbox.tar` for this deploy
- [ ] 2.4 Keep `VISTA_ENV` at `dev` until app-level SSO exists; never treat
      `launch.sh --prod` as production auth
- [ ] 2.5 Ignore client-supplied `X-Vista-User-Email` in the deployed config
- [ ] 2.6 Document Tool Registry / Wormhole as the compliant execution
      follow-up; document that MicroVM on AmSC needs an RFC amendment

## 3. Hardened chart

- [ ] 3.1 Create `chart/` from current `main` (nginx chart + platform
      gateway companions as reference; do not rebase `feat/helm`)
- [ ] 3.2 Deployment: `replicas: 1`, `Recreate`, non-root `securityContext`,
      dropped caps, no `privileged`, sandbox/Globus off
- [ ] 3.3 NetworkPolicy limiting ingress to the gateway / mesh pattern
      platform uses on ms-dev
- [ ] 3.4 HTTPRoute + OIDC companion (PingAM) — not an open `/` route
- [ ] 3.5 PVC for `/data`; probes that do not wait on Globus or sandbox
- [ ] 3.6 ExternalSecret (or documented path) for model/MAG credentials and
      other secrets; empty defaults in git
- [ ] 3.7 Values prefer MAG; optional interim direct-provider flag marked
      non-compliant in README
- [ ] 3.8 `helm lint` / `helm template` clean with defaults

## 4. Identity documentation

- [ ] 4.1 README states: gateway OIDC ≠ per-user VISTA identity; shared-admin
      until SSO; `VISTA_ENV=prod` is 501 today
- [ ] 4.2 File / link the follow-up change for app-level SSO + Keycard
      validation (RFC §3B)
- [ ] 4.3 Restrict who may use the hostname while shared-admin remains

## 5. CI / supply chain

- [ ] 5.1 Chart lint + template on MRs and in `./scripts/ci-local.sh`
- [ ] 5.2 On `main`: build image (GitLab token BuildKit secret), scan, retain
      report; push `vista/vista-server` and `charts/vista-server` in
      `890890990154`, versions from the same commit
- [ ] 5.3 Document CI variables (`AWS_ACCOUNT_ID`, `AWS_OIDC_ROLE_ARN`) and
      trust branches (`main`, `dev`)
- [ ] 5.4 Do not publish from merge request pipelines

## 6. First compliant deploy

- [ ] 6.1 ArgoCD sync: pod Ready; OIDC login reaches the UI
- [ ] 6.2 Confirm local code-execution tools are unavailable
- [ ] 6.3 Confirm inference path (MAG or documented interim)
- [ ] 6.4 Restart pod; SQLite/KB state persists
- [ ] 6.5 README: verification + rollback (`targetRevision`)
- [ ] 6.6 Agree with platform on retiring `amsc-vista-nginx` after cutover
- [ ] 6.7 PR CI stays hermetic (no cluster / KVM / live sandbox required)
