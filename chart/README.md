# VISTA — Helm Chart

This chart deploys VISTA to EKS via ArgoCD, following the AmSC hub-and-spoke GitOps model.
The app team owns this chart; the platform team manages ArgoCD Applications and environment-specific values.

## Architecture

```
Browser
  │
  ▼
Kong Gateway (if-kong)  ←── Globus OIDC session auth
  │
  ▼ :80
vista-svc (ClusterIP)
  │
  ▼ :3000
vista pod
  ├── Next.js UI         :3000  (external entry point)
  ├── FastAPI backend    :8001  (internal; Next.js BFF proxies to it)
  ├── vista MCP server   :8000  (internal; HPC, RAG, file tools)
  └── dev-mcp-server     stdio  (one per active chat session; runs sandbox)
        └── microsandbox /dev/kvm  (MicroVM per code execution)
```

All four processes are started by `launch.sh` inside a single pod using the
`aws/Dockerfile.server` all-in-one image. State (SQLite DB, ChromaDB, HuggingFace
model cache, per-user sandbox volumes) is persisted on a 50 Gi `ebs-gp3` PVC at `/data`.

## Chart resources

| Resource | Name | Purpose |
|----------|------|---------|
| Namespace | `vista` | Isolated namespace for all VISTA resources |
| ConfigMap | `vista-config` | Non-sensitive runtime env vars |
| Secret | `vista-secrets` | Sensitive credentials (API keys, tokens) |
| ExternalSecret | `vista-secrets` / `vista-oidc-credentials` | Pull secrets from AWS Secrets Manager (optional) |
| PersistentVolumeClaim | `vista-data` | 50 Gi GP3 volume mounted at `/data` |
| Deployment | `vista` | Single-pod, `Recreate` strategy (required by ReadWriteOnce PVC) |
| Service | `vista-svc` | ClusterIP 80 → 3000 |
| KongPlugin | `vista-oidc` | Globus session OIDC for browser login |
| KongPlugin | `vista-login-termination` | Redirects browser to `/` after OIDC code exchange |
| HTTPRoute | `vista-login` | `/kong-login` and `/auth` — OIDC initiation and callback |
| HTTPRoute | `vista-static` | `/_next/` and `/favicon.ico` — public (no auth, needed before session exists) |
| HTTPRoute | `vista-main` | `/` catch-all — OIDC protected |

---

## Deployment checklist

### App team tasks

#### 1. Register a Globus OIDC application

VISTA uses Globus as its identity provider. You need a Globus OAuth2 client for the
session cookie flow that Kong enforces at the ingress.

1. Go to [developers.globus.org](https://developers.globus.org) and create a new app.
2. Set the redirect URI to `https://vista.dev.american-science-cloud.org/auth`.
3. Set the logout redirect URI to `https://vista.dev.american-science-cloud.org`.
4. Note the **Client ID** and **Client Secret** — you will need them in step 3 below.

#### 2. Build the container image

The `aws/Dockerfile.server` bundles all four processes (Next.js, FastAPI, vista MCP,
sandbox image tar) into a single image. Building it requires a GitLab token to fetch the
private `amscrot-py` dependency.

```bash
# From the vista repo root
export GITLAB_TOKEN=<your-gitlab-pat>
bash aws/build-image.sh
```

The script builds the microsandbox OCI image, exports it as a tar, then builds the main
server image and tags it as `vista-server:latest`.

Authenticate to ECR and push:

```bash
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin \
      879946464637.dkr.ecr.us-east-1.amazonaws.com

docker tag vista-server:latest \
  879946464637.dkr.ecr.us-east-1.amazonaws.com/interfaces/vista:latest

docker push 879946464637.dkr.ecr.us-east-1.amazonaws.com/interfaces/vista:latest
```

> The ECR repository (`interfaces/vista`) must be created by the platform team first —
> see platform team step 1.

#### 3. Generate the Fernet encryption key

The backend encrypts per-user HPC tokens stored in the database. This key must be stable
across pod restarts (it lives in the PVC-backed SQLite DB).

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

Store the output as `VISTA_BACKEND_ENCRYPTION_KEY` in your secrets source (see step 4).

#### 4. Provision secrets

Secrets can be provided in two ways depending on the environment.

**Option A — Dev bootstrap (ArgoCD inline values):**

Set `appSecrets.create: true` and inject values via the ArgoCD Application `helm.values`.
This avoids the need for AWS Secrets Manager during early development. Do not commit
real values to the chart.

```yaml
# In the ArgoCD Application helm.values block:
appSecrets:
  create: true
  openaiApiKey: "..."                      # AmSC inference API key
  encryptionKey: "..."                     # Fernet key from step 3
  hfToken: "..."                           # HuggingFace token (google/embeddinggemma-300m)
  globusSetupKey: "..."                    # Headless Globus Connect Personal setup key
  globusOdoRefreshToken: "..."             # Globus refresh token for OLCF Odo
  globusFrontierRefreshToken: "..."        # Globus refresh token for Frontier
  omdApiKey: "..."                         # OpenMetadata catalog API key (optional)
```

**Option B — Production (AWS Secrets Manager via ESO):**

Create a secret in AWS Secrets Manager at `/amsc/dev/vista` with the same keys listed
above. Then set in the Application values:

```yaml
appSecrets:
  create: false
  externalSecret:
    enabled: true
    secretsManagerPath: /amsc/dev/vista
```

ESO will sync the secret to the `vista` namespace every hour. The platform team must
grant the `infra-vault` IRSA role read access to this path — see platform team step 3.

Similarly, create `/amsc/dev/vista-oidc` with keys `clientId`, `clientSecret`,
`sessionSecret`, and `redisHost`. Then enable:

```yaml
oidc:
  externalSecret:
    enabled: true
    secretsManagerPath: /amsc/dev/vista-oidc
```

#### 5. Package and push the Helm chart

Every deployment uses a chart version baked with the image commit SHA so ArgoCD can
detect changes and trigger a sync.

```bash
# Authenticate Helm to ECR
aws ecr get-login-password --region us-east-1 \
  | helm registry login --username AWS --password-stdin \
      879946464637.dkr.ecr.us-east-1.amazonaws.com

# Bump version in Chart.yaml to match the image SHA, then package
CHART_VERSION="0.1.0-$(git rev-parse --short HEAD)"
sed -i "s/^version:.*/version: ${CHART_VERSION}/" chart/Chart.yaml
helm package chart/

# Push as OCI artifact to ECR
helm push vista-${CHART_VERSION}.tgz \
  oci://879946464637.dkr.ecr.us-east-1.amazonaws.com/charts
```

> The `charts/vista` ECR repository must be created by the platform team — see
> platform team step 1.

#### 6. Render and inspect templates locally

Before deploying, verify the rendered manifests look correct:

```bash
# Render with default values
helm template vista chart/

# Render with environment-specific overrides to simulate what ArgoCD will apply
helm template vista chart/ \
  --set host=vista.dev.american-science-cloud.org \
  --set image.tag=abc1234 \
  --set appSecrets.create=true \
  --set appSecrets.openaiApiKey=test

# Lint
helm lint chart/
```

---

### Platform team tasks

#### 1. Create ECR repositories

VISTA needs two ECR repositories in the dev account — one for the container image and
one for the Helm chart (stored as an OCI artifact).

In `amsc-platform/infra/environments/dev/terraform.tfvars`, add:

```hcl
ecr_repository_names = [
  # ... existing entries ...
  "interfaces/vista",   # container image
  "charts/vista",       # OCI Helm chart
]
```

Open an MR and `tofu apply` in the dev environment. ECR repos use immutable tags, so
every chart push must use a unique version (CI enforces this with the commit SHA suffix).

#### 2. Create the ArgoCD Application

Create `argocd/apps/dev/vista.yaml` in the `amsc-platform` repo. Sync-wave `"4"` ensures
VISTA deploys after Kong, ESO, and the platform secrets are all healthy.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: vista
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "4"
spec:
  project: default
  source:
    chart: vista
    repoURL: 879946464637.dkr.ecr.us-east-1.amazonaws.com
    targetRevision: 0.1.0-<initial-sha>
    helm:
      values: |
        namespace: vista
        host: vista.dev.american-science-cloud.org
        image:
          repository: 879946464637.dkr.ecr.us-east-1.amazonaws.com/interfaces/vista
          tag: <initial-sha>
        gateway:
          name: kong
          namespace: if-kong
        oidc:
          # Use inline values for dev bootstrap; switch to externalSecret for production
          clientId: "<globus-client-id>"
          clientSecret: "<globus-client-secret>"
          sessionSecret: "<random-32-char-string>"
          redis:
            host: "<elasticache-endpoint>"
        appSecrets:
          create: true
          openaiApiKey: "<amsc-inference-api-key>"
          encryptionKey: "<fernet-key>"
          hfToken: "<huggingface-token>"
          globusSetupKey: "<globus-setup-key>"
          globusOdoRefreshToken: "<odo-refresh-token>"
          globusFrontierRefreshToken: "<frontier-refresh-token>"
  destination:
    server: https://kubernetes.default.svc
    namespace: vista
  syncPolicy:
    automated:
      prune: false
      selfHeal: false
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

The `targetRevision` and `image.tag` fields are updated automatically by the CI pipeline
via `deploy-notify.yml` after each successful image build.

#### 3. Grant Secrets Manager access (production only)

When switching to Option B (ESO-managed secrets), the `infra-vault` IRSA role needs read
access to the VISTA secret paths. In `amsc-platform/infra/environments/dev/main.tf`,
extend the `infra-vault` IAM policy to include:

```
arn:aws:secretsmanager:us-east-1:879946464637:secret:/amsc/dev/vista*
```

Then create the secrets in AWS Secrets Manager:

```bash
# App secrets
aws secretsmanager create-secret \
  --name /amsc/dev/vista \
  --region us-east-1 \
  --secret-string '{
    "OPENAI_API_KEY": "...",
    "VISTA_BACKEND_ENCRYPTION_KEY": "...",
    "HF_TOKEN": "...",
    "GLOBUS_SETUP_KEY": "...",
    "VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN": "...",
    "VISTA_MCP_FRONTIER_GLOBUS_REFRESH_TOKEN": "...",
    "VISTA_MCP_OMD_API_KEY": "..."
  }'

# OIDC credentials (used by ExternalSecret template engine to build the Kong config YAML)
aws secretsmanager create-secret \
  --name /amsc/dev/vista-oidc \
  --region us-east-1 \
  --secret-string '{
    "clientId": "...",
    "clientSecret": "...",
    "sessionSecret": "...",
    "redisHost": "..."
  }'
```

#### 4. Provision a KVM-capable node group (if not already available)

The VISTA pod runs with `privileged: true` and mounts `/dev/kvm` to support microsandbox
MicroVM isolation for the code execution sandbox. Standard EKS managed node groups with
Amazon Linux 2 or AL2023 on `m5` or `c5` instance types already expose `/dev/kvm`
through the hypervisor.

Verify KVM is available on your node pool:

```bash
kubectl debug node/<node-name> -it --image=ubuntu -- lsmod | grep kvm
```

If KVM is not available and you cannot use a privileged node group, set
`kvm.enabled: false` and `config.sandboxMode: docker` in the Application values.
This runs sandboxes as Docker containers instead of MicroVMs, which is less isolated
but avoids the privileged requirement. Note that `docker` mode is not pre-configured
in the current image — you would also need to run a Docker-in-Docker sidecar or
install Docker on the node.

#### 5. Register the Globus redirect URI

Once the domain `vista.dev.american-science-cloud.org` is live and DNS is pointing at
the Kong NLB, register the redirect URI with the Globus app created in app team step 1:

- **Redirect URI:** `https://vista.dev.american-science-cloud.org/auth`
- **Logout URI:** `https://vista.dev.american-science-cloud.org`

This is required before any user can log in.

---

## Values reference

| Value | Default | Description |
|-------|---------|-------------|
| `replicaCount` | `1` | Number of pods. Keep at 1 — PVC is ReadWriteOnce |
| `image.repository` | dev ECR | Container image repository |
| `image.tag` | `latest` | Image tag; CI bakes the commit SHA |
| `namespace` | `vista` | Kubernetes namespace |
| `host` | `vista.dev.american-science-cloud.org` | Public hostname |
| `gateway.name` | `kong` | Kong Gateway name |
| `gateway.namespace` | `if-kong` | Kong Gateway namespace |
| `oidc.clientId` | `""` | Globus OAuth2 client ID |
| `oidc.clientSecret` | `""` | Globus OAuth2 client secret |
| `oidc.sessionSecret` | `""` | Session cookie encryption key |
| `oidc.redis.host` | `""` | ElastiCache Redis endpoint for session storage |
| `oidc.externalSecret.enabled` | `false` | Pull OIDC config from AWS SM via ESO |
| `oidc.externalSecret.secretsManagerPath` | `""` | SM path, e.g. `/amsc/dev/vista-oidc` |
| `appSecrets.create` | `false` | Create Kubernetes Secret from inline values |
| `appSecrets.externalSecret.enabled` | `false` | Pull app secrets from AWS SM via ESO |
| `appSecrets.externalSecret.secretsManagerPath` | `""` | SM path, e.g. `/amsc/dev/vista` |
| `config.vistaEnv` | `prod` | `dev` or `prod` |
| `config.openaiBaseUrl` | AmSC inference API | LLM endpoint |
| `config.vistaModel` | `""` | PydanticAI model spec, e.g. `openai:claude-sonnet-4-5` |
| `config.sandboxMode` | `microsandbox` | Code sandbox backend |
| `persistence.size` | `50Gi` | PVC size for `/data` |
| `persistence.storageClass` | `ebs-gp3` | Platform-standard GP3 StorageClass |
| `kvm.enabled` | `true` | Mount `/dev/kvm` and run pod as privileged |
| `resources.requests.cpu` | `500m` | CPU request |
| `resources.requests.memory` | `4Gi` | Memory request (HF model + ChromaDB are heavy) |
| `resources.limits.cpu` | `4000m` | CPU limit |
| `resources.limits.memory` | `8Gi` | Memory limit |

## CI/CD integration

Follow the standard AmSC hub-and-spoke pipeline pattern. Include `deploy-notify.yml`
from `amsc-platform` in the VISTA `.gitlab-ci.yml`:

```yaml
include:
  - project: 'amsc2/infrastructure-and-services/infrastructure-services/container-services-platform/amsc-platform'
    file: 'ci/deploy-notify.yml'
    ref: dev

variables:
  SERVICE_NAME: vista           # must match the ArgoCD Application name
  PLATFORM_ENV: dev
  AWS_ACCOUNT_ID: "879946464637"
  AWS_OIDC_ROLE_ARN: arn:aws:iam::879946464637:role/gitlab-ci-ecr-pusher-dev
```

On each push to `main`, CI will:
1. Build the container image (with `GITLAB_TOKEN` for `amscrot-py`)
2. Scan with Trivy (fails on CRITICAL)
3. Push image to `interfaces/vista` in ECR
4. Package and push the Helm chart to `charts/vista` in ECR
5. Open an MR in `amsc-platform` updating `targetRevision` in `argocd/apps/dev/vista.yaml`
6. ArgoCD detects the new chart version and syncs the deployment
