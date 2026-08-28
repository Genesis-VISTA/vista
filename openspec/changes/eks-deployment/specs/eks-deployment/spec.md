## ADDED Requirements

### Requirement: Deployable container image

The repository SHALL provide a reproducible all-in-one container image that
starts the UI, backend, `vista_mcp_server`, and Globus endpoint without an
interactive login and without a build step at container start.

#### Scenario: Image starts without interactive input

- **WHEN** the image is run with required environment variables supplied
- **THEN** it SHALL start all bundled processes non-interactively
- **AND** it MUST NOT require a terminal, browser login, or in-container build

#### Scenario: Private dependency is built from a secret, not baked

- **WHEN** the image is built
- **THEN** the private `amscrot-py` credential SHALL be supplied as a build
  secret
- **AND** the credential MUST NOT be present in any published image layer

### Requirement: Helm chart for ArgoCD deployment

A Helm chart SHALL exist in the repository that renders the Kubernetes
resources needed to run VISTA on the AmSC EKS cluster via ArgoCD.

#### Scenario: Chart renders and lints with defaults

- **WHEN** `helm lint` and `helm template` run against the chart with default
  values
- **THEN** both SHALL succeed with no errors
- **AND** CI SHALL run them on every merge request

#### Scenario: Chart renders with environment overrides

- **WHEN** the chart is rendered with host, image tag, and secret overrides
  equivalent to those an ArgoCD Application supplies
- **THEN** rendering SHALL succeed
- **AND** the resulting manifests SHALL reference the overridden values

#### Scenario: Single-writer topology is enforced

- **WHEN** the Deployment is rendered
- **THEN** `replicas` SHALL be 1
- **AND** the update strategy SHALL be `Recreate`
- **AND** the chart SHALL document that SQLite on a ReadWriteOnce volume and
  the process-local agent pool require a single writer

### Requirement: Least-privilege sandbox runtime

The deployment SHALL request KVM device access rather than a privileged pod.

#### Scenario: No privileged pods by default

- **WHEN** the chart is rendered with default values
- **THEN** no container SHALL set `privileged: true`
- **AND** KVM SHALL be requested through device access on a KVM-capable node
  pool

#### Scenario: Sandbox mode is configurable

- **WHEN** an operator sets the sandbox mode value
- **THEN** the rendered configuration SHALL propagate it to `dev_mcp_server`
- **AND** supported values SHALL be limited to those the code implements

#### Scenario: Pod starts when KVM is unavailable

- **WHEN** the deployment runs on nodes without KVM and KVM is disabled in values
- **THEN** the pod SHALL still reach a ready state
- **AND** sandbox-backed tools SHALL fail with an explicit, actionable error
- **AND** the pod MUST NOT crash-loop

### Requirement: Sandbox image availability without registry egress

The sandbox guest image SHALL be available to the sandbox runtime at first use
without pulling from a remote registry.

#### Scenario: Baked image tar is loaded at startup

- **WHEN** `dev_mcp_server` starts with the OCI image tar environment variable set
- **THEN** it SHALL load that tar into the sandbox runtime if the image is absent
- **AND** the first sandbox spawn MUST NOT require a registry pull

#### Scenario: Missing tar fails clearly

- **WHEN** the configured tar path does not exist
- **THEN** startup SHALL emit an explicit error naming the path
- **AND** it MUST NOT silently fall back to a network pull

### Requirement: Persistent state

Runtime state SHALL persist across pod restarts on a mounted volume.

#### Scenario: State survives a restart

- **WHEN** the pod is deleted and rescheduled with the same volume
- **THEN** the SQLite database, knowledge base stores, per-user sandbox volumes,
  Globus endpoint configuration, and sandbox runtime home SHALL still be present

#### Scenario: Sandbox runtime home path stays shallow

- **WHEN** the sandbox runtime home is configured on the persistent volume
- **THEN** its path SHALL be shallow enough to avoid socket path length limits
- **AND** the constraint SHALL be documented in the chart

### Requirement: Resource limits consistent with sandbox concurrency

Pod resource limits SHALL be reconcilable with the maximum number of concurrent
sandboxes the backend admits.

#### Scenario: Configured concurrency fits the memory limit

- **WHEN** the chart is rendered
- **THEN** maximum concurrent sandboxes multiplied by per-sandbox memory SHALL
  fit within the pod memory limit, allowing for process overhead
- **AND** the chart SHALL document how the two values relate

#### Scenario: Startup probe tolerates cold start

- **WHEN** the pod starts cold, including model cache and knowledge base loading
- **THEN** the startup probe SHALL allow enough time for all processes to become
  ready before liveness checks begin

### Requirement: Secrets are never committed

Sensitive configuration SHALL be supplied at deploy time and SHALL NOT be
present in the repository.

#### Scenario: No real secret values in the chart

- **WHEN** the chart is inspected
- **THEN** all secret-valued keys SHALL default to empty
- **AND** the chart SHALL support both a directly-created Secret and an external
  secret store

#### Scenario: Encryption key is required in production mode

- **WHEN** the backend runs with production settings
- **THEN** a database encryption key SHALL be required
- **AND** the chart SHALL document that this key must remain stable across
  restarts because encrypted per-user credentials live on the volume

### Requirement: Interim identity posture is explicit and bounded

Until production SSO exists, the deployment SHALL make its identity limitations
explicit and SHALL prevent identity spoofing from outside the gateway.

#### Scenario: Production auth gap is documented as blocking

- **WHEN** deployment documentation is read
- **THEN** it SHALL state that production mode returns 501 on all routes because
  SSO is unimplemented
- **AND** it SHALL state that development mode resolves callers lacking an
  identity header to the seeded administrator
- **AND** it SHALL name production SSO as a blocking dependency for multi-user use

#### Scenario: Identity headers cannot be supplied by clients

- **WHEN** a request arrives from outside the cluster carrying a user identity header
- **THEN** the gateway SHALL strip or overwrite it before the request reaches the backend
- **AND** the backend SHALL NOT be reachable bypassing the gateway

#### Scenario: Deployment is restricted while interim

- **WHEN** the deployment runs before SSO lands
- **THEN** access SHALL be restricted to a documented trusted user set
- **AND** the shared-identity consequences for projects, chat history, stored
  HPC credentials, and sandbox files SHALL be documented

### Requirement: Service seam preserved for future split

The deployment SHALL NOT introduce coupling that prevents extracting
`vista_mcp_server` into its own workload later.

#### Scenario: MCP server is addressed by URL

- **WHEN** the backend is configured
- **THEN** it SHALL address `vista_mcp_server` through a configurable URL
- **AND** that URL SHALL be overridable to an off-pod address without code changes

### Requirement: Publishing pipeline

CI SHALL build and publish the deployment artifacts with traceable versions.

#### Scenario: Image and chart are versioned together

- **WHEN** the publishing pipeline runs
- **THEN** the image tag and chart version SHALL both derive from the same commit
- **AND** each published chart version SHALL be unique so immutable-tag
  registries accept it

#### Scenario: Image is scanned before publish

- **WHEN** the image is built in CI
- **THEN** it SHALL be vulnerability-scanned
- **AND** the documented failure threshold SHALL be enforced

#### Scenario: Publishing does not run on merge requests

- **WHEN** a merge request pipeline runs
- **THEN** chart lint and template checks SHALL run
- **AND** image or chart publication MUST NOT run

### Requirement: Platform prerequisites documented

The chart SHALL document every prerequisite the platform team must provide.

#### Scenario: Prerequisites are enumerated

- **WHEN** the chart documentation is read
- **THEN** it SHALL enumerate container and chart registry repositories, the
  ArgoCD Application and sync ordering, gateway routing and OIDC configuration,
  the KVM-capable node pool, persistent storage class, secret store paths, and
  the workload identity grants required to read them

#### Scenario: Deployment readiness is verifiable

- **WHEN** the deployment is applied
- **THEN** documentation SHALL describe how to verify the pod is healthy, the
  gateway routes correctly, state persists across a restart, and whether the
  sandbox is functional
