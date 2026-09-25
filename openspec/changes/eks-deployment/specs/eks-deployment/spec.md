## ADDED Requirements

### Requirement: Platform questions are answered in the architecture

The deployment SHALL implement the decisions recorded against
`amsc-platform` `docs/review/vista-questions.md`: in-cluster on `amsc-ms-dev`,
no EC2 app host, no local arbitrary code execution, MAG-oriented inference,
IRSA/External Secrets, full OCI GitOps, and a hardened authenticated chart.

#### Scenario: No out-of-cluster app host

- **WHEN** the deployment is described or rendered
- **THEN** the application runtime SHALL be a Kubernetes workload on
  `amsc-ms-dev`
- **AND** it MUST NOT require an EC2 instance or a reverse-proxy tunnel from
  the cluster to a VM

#### Scenario: Account boundary is Model Services

- **WHEN** images and charts are published
- **THEN** they SHALL target account `890890990154`
- **AND** the ArgoCD Application SHALL live under `argocd/apps/amsc-ms-dev/`

### Requirement: RFC §5E-compliant execution posture

The deployed VISTA SHALL NOT execute arbitrary LLM-generated code on platform
infrastructure. Local sandbox backends that exist for development MUST NOT be
enabled in this deployment.

#### Scenario: Microsandbox is not enabled

- **WHEN** the chart is rendered with default (production-beta) values
- **THEN** `microsandbox` SHALL NOT be the configured sandbox mode
- **AND** no container SHALL require `/dev/kvm` or `privileged: true`

#### Scenario: Sandbox tools fail closed

- **WHEN** an agent invokes a local code-execution tool in the deployed
  environment
- **THEN** the call SHALL fail with an explicit error that execution is
  unavailable under platform policy
- **AND** the pod MUST NOT crash-loop

#### Scenario: Compliant execution path is documented

- **WHEN** deployment documentation is read
- **THEN** it SHALL state that Tool Registry / facility Wormhole is the
  RFC-compliant execution model
- **AND** it SHALL state that restoring MicroVM execution on AmSC requires an
  RFC §5E amendment, not only a chart value change

### Requirement: Deployable container image

The repository SHALL provide a reproducible all-in-one container image that
starts the UI, backend, and `vista_mcp_server` without interactive login,
without Globus setup, and without a build step at container start.

#### Scenario: Image starts without interactive input

- **WHEN** the image is run with required environment variables and with
  Globus and local sandbox left disabled
- **THEN** it SHALL start the UI, backend, and `vista_mcp_server`
  non-interactively
- **AND** it MUST NOT require a terminal, browser login, Globus setup key,
  `/dev/kvm`, or an in-container build

#### Scenario: Private dependency is built from a secret, not baked

- **WHEN** the image is built
- **THEN** the private `amscrot-py` credential SHALL be supplied as a build
  secret
- **AND** the credential MUST NOT be present in any published image layer

### Requirement: Hardened Helm chart for ArgoCD

A Helm chart SHALL render the Kubernetes resources for VISTA on `amsc-ms-dev`
with security defaults that meet platform expectations for apps beyond a
hello world.

#### Scenario: Chart renders and lints

- **WHEN** `helm lint` and `helm template` run with default values
- **THEN** both SHALL succeed
- **AND** CI SHALL run them on every merge request

#### Scenario: Security context is set

- **WHEN** the Deployment is rendered with default values
- **THEN** containers SHALL NOT run as root
- **AND** `privileged: true` SHALL NOT be set
- **AND** unnecessary capabilities SHALL be dropped

#### Scenario: NetworkPolicy restricts ingress

- **WHEN** the chart is rendered with default values
- **THEN** a NetworkPolicy SHALL limit which sources can reach the pod
- **AND** the backend MUST NOT be exposed as a public NodePort/LoadBalancer

#### Scenario: Gateway auth companion is present

- **WHEN** the chart is rendered with default values
- **THEN** the HTTPRoute (or companion route pattern) SHALL attach the
  platform OIDC plugin configuration used on `amsc-ms-dev` (PingAM)
- **AND** it MUST NOT ship as an unauthenticated catch-all `/` route the way
  the nginx hello world did

#### Scenario: Single-writer topology

- **WHEN** the Deployment is rendered
- **THEN** `replicas` SHALL be 1
- **AND** the update strategy SHALL be `Recreate`

#### Scenario: Production auth mode is not claimed early

- **WHEN** the chart is rendered with default values
- **THEN** it MUST NOT set `VISTA_ENV=prod` until app-level SSO exists

### Requirement: Model access prefers MAG

Inference configuration SHALL prefer the Model Access Gateway. Direct provider
access, if used, SHALL be explicitly interim.

#### Scenario: Default values point at MAG

- **WHEN** the chart documentation and default values are read
- **THEN** the preferred inference path SHALL be the MAG
- **AND** any direct-provider values flag SHALL be documented as interim
  FinOps/DLP debt with a migration task

### Requirement: Identity and secrets follow platform patterns

Secrets SHALL use External Secrets and workload identity. Client-supplied
identity headers SHALL NOT select the acting user.

#### Scenario: No committed secrets

- **WHEN** the chart is inspected
- **THEN** secret-valued keys SHALL default to empty
- **AND** the chart SHALL reference an external secret store path pattern
  agreed with platform

#### Scenario: Client identity header is ignored

- **WHEN** a request carries `X-Vista-User-Email`
- **THEN** the deployed configuration SHALL NOT let that header select the
  acting user

#### Scenario: Multi-user limitation is documented

- **WHEN** deployment documentation is read
- **THEN** it SHALL state that gateway OIDC does not by itself create
  per-user VISTA identity
- **AND** it SHALL name app-level SSO / Keycard validation as a blocking
  follow-up before broad multi-user access

### Requirement: Persistent state

Runtime state this deploy writes SHALL persist across restarts on a volume.

#### Scenario: State survives a restart

- **WHEN** the pod is deleted and rescheduled with the same volume
- **THEN** the SQLite database and knowledge-base stores SHALL still be present

### Requirement: OCI publishing and GitOps

CI SHALL publish image and chart to Model Services ECR with commit-derived
versions. Merge request pipelines SHALL NOT publish.

#### Scenario: Image and chart versioned together

- **WHEN** the publishing pipeline runs on the default branch
- **THEN** the image tag and chart version SHALL derive from the same commit
- **AND** the image SHALL push to
  `890890990154.dkr.ecr.us-east-1.amazonaws.com/vista/vista-server`
- **AND** the chart SHALL push to
  `890890990154.dkr.ecr.us-east-1.amazonaws.com/charts/vista-server`

#### Scenario: Image is scanned

- **WHEN** the image is built for publish
- **THEN** a vulnerability scan SHALL run and the report SHALL be retained

#### Scenario: MR pipelines do not publish

- **WHEN** a merge request pipeline runs
- **THEN** chart lint/template SHALL run
- **AND** publication MUST NOT run

### Requirement: Platform prerequisites documented

Chart documentation SHALL list platform prerequisites for this architecture
and SHALL NOT list EC2, KVM node pools, or cross-account VM networking as
required for the first compliant deploy.

#### Scenario: Prerequisites match the decisions

- **WHEN** the chart README is read
- **THEN** it SHALL enumerate ECR repos, GitLab OIDC trust, ArgoCD
  Application, DNS, PingAM redirect URIs, secret paths, IRSA, and namespace
- **AND** it SHALL NOT require an EC2 instance or `/dev/kvm` for go-live

#### Scenario: Verification and rollback

- **WHEN** the deployment is applied
- **THEN** docs SHALL describe verifying OIDC login, pod readiness, sandbox
  tools unavailable, and rollback via prior chart `targetRevision`
