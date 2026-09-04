## Why

Running VISTA today needs Docker, a HuggingFace account with terms accepted for a gated
model, a token for `code.ornl.gov`, credentials for a second private GitLab, and a
hand-assembled `.env`. `scripts/build.sh:24` hard-requires the private `amsc2` dependency
and `scripts/launch.sh:28` forces an interactive Globus login, so a researcher's first
`./launch.sh` usually aborts before any service starts. A researcher should download one
file, run one command, and paste one API key.

## What Changes

- Add `build_local_package.sh`: produces a per-platform archive holding prebuilt virtual
  environments, a CPython interpreter, `uv`, a Next.js production build, the embedding
  weights, the molten-salt corpus with a **prebuilt** vector store, and the sandbox image
  as a tarball. It preflights its own prerequisites and ends with a relocation smoke test.
- Add a `vista` launcher: one command that performs first-run setup — loading the sandbox
  image, creating state under `~/.vista`, seeding — then starts the services.
- **Boot with no configuration.** `VISTA_BACKEND_MODEL` gains a default and the
  OpenAI-compatible endpoint becomes a settings field, so the inference API key is the only
  value a researcher supplies, entered in the existing user-settings modal.
- Replace the gated `google/embeddinggemma-300m` with `microsoft/harrier-oss-v1-270m`
  (MIT, ungated, 640-dimension), bundled — removing HuggingFace from first run entirely.
- Seed from a bundled snapshot: a `LocalRepoClient` third branch in
  `backend/src/vista_backend/db/seed.py`, so the four existing data gates fire unchanged.
- Make Globus endpoint setup conditional **and non-fatal** (`scripts/launch.sh:28`), which
  also repairs `aws/Dockerfile.server:78` — that container cannot boot today.
- Remove the dead "Frontier Globus token" field from the UI. Nothing reads it.

## Capabilities

### New Capabilities

- `zero-config-startup`: VISTA starts with no configuration file present, states what is
  unavailable until configured, and accepts its inference credentials through the UI.
- `laptop-distribution`: VISTA ships as a self-contained, relocatable, per-platform
  artifact that runs with no host prerequisites beyond the platform floor.

### Modified Capabilities

- (none — `hpc-job-contracts` and `testing-ci` are both testing-scoped and describe no
  startup or distribution behavior)

## Impact

- **New**: `build_local_package.sh`, `vista`, `LocalRepoClient`.
- **Modified**: `backend/.../config.py` (model default, endpoint, bundled-data path),
  `agents.py` (provider factory), `db/seed.py`, `build_rag.py:640`,
  `vista_mcp_server/config.py:218`, `scripts/launch.sh`, `ui/next.config.mjs`,
  `UserSettingsModal.tsx`, `ui/lib/user.ts`.
- **Unchanged**: `scripts/build.sh`, `scripts/ci-local.sh`, all test suites, `lib/iri.py`.
- No CI impact: PR pipelines reference no credentials and already sync without `--extra hpc`.

## Non-goals

VISTAGuard; Globus/GCP and therefore Odo and Frontier (deferred to the S3 replacement,
Perlmutter stays in scope); native Windows (no microsandbox wheel — WSL2 documented);
Intel Macs and musl-only Linux; multi-user deployment and SSO; the microsandbox 0.6
upgrade; query-side prompt prefixes; unifying the embedding-model name into one setting; a
dimension-mismatch guard for existing vector stores; the `README.md` rewrite.
