## Context

See `proposal.md` — Why. Decision identifiers below (R, C, E, D, P, L, H) match the
reviewed design surface, so a comment on "E2" or "H2" resolves to one decision here.

Constraints that shape the approach:

- **The sandbox needs a container runtime only at build time.** `msb` ships inside the
  `microsandbox` wheel (`microsandbox/_bundled/bin/msb`, 23 MB, plus a 24 MB `libkrunfw`),
  needs no daemon and no root, and `msb load -i <tar> -t <tag>` imports a `docker save`
  archive fully offline. Verified empirically: a microVM runs as a plain user process
  parented to Python with no OCI runtime in its process tree. `PullPolicy.NEVER` is already
  set (`microsandbox_sandbox.py:111`).
- **One required setting blocks a configuration-free boot.** `model` at
  `backend/src/vista_backend/config.py:72` is the only field with no default; everything
  else already defaults.
- **A per-user credential store already exists.** `s3m_token` / `nersc_iri_token` are
  `EncryptedStr` columns (`schemas.py:504`), edited in `UserSettingsModal.tsx`. There is
  one user, so that row *is* the deployment configuration.
- **Agents are pooled behind a factory with post-commit eviction**
  (`services/project_agent.py:92-97`, `:143-156`), so a credential change propagates
  without a restart.
- **Seeding hangs off one truthy variable.** Four gates test `vista_data_client`
  (`seed.py:150`, `:186`, `:243`, `:252`), and `_build_knowledge_base` returns early when a
  store exists (`:101-102`).
- **Two unconditional lines break a minimal host.** `scripts/build.sh:24` requires the
  private `amsc2` dependency, and `scripts/launch.sh:28` runs Globus setup which, on any
  non-Linux host, re-executes itself in a Linux container *before* any Globus logic
  (`launch_globus.py:162-163`) and dies without a container runtime (`:80-84`).

## Goals / Non-Goals

**Goals:**

- Every prerequisite is satisfied by the packager, never the recipient.
- With no configuration recorded, existing checkouts and the container image behave as before.
- Failures surface at build time, on the builder's machine, not on the recipient's.

**Non-Goals:**

- Reducing the artifact below roughly 4.5 GB. Dependency-set edits are deliberately avoided.
- Headless or CI use of the artifact. It is interactive, for one researcher on one machine.
- Fixing adjacent defects the investigation surfaced (see proposal Non-goals).

## Decisions

### Runtime: bundle the sandbox image, don't build it on the host (R1–R4)

The image ships as a `docker save` tarball and is imported by `msb` on first run. Docker
becomes a build-time tool for the packager only.

*Alternative rejected — registry pull.* `msb pull` also avoids a local runtime, but it
requires network at install time and a published image, and defeats the offline goal.

Two constraints follow. The bundled `msb` binary carries a
`com.apple.security.hypervisor` entitlement that grants unprivileged hypervisor access, so
**packaging must not strip, re-sign, or repackage it**, and the archive format must
preserve extended attributes (R2). The platform floor is Apple Silicon on macOS, and on
Linux `/dev/kvm` access plus glibc ≥ 2.28 (R3) — so "no Docker" is not "no privileged
setup" on Linux, which needs a one-time group grant. Stay on `microsandbox==0.5.7` (R4):
everything needed is verified present, and folding in the 0.6 upgrade would give any
sandbox regression two candidate causes.

### Configuration: default the model, put the credential in the UI (C1–C6)

`model` keeps its type and gains the default `openai:claude-sonnet` — written with the
provider prefix deliberately, since a bare name resolves through a legacy prefix map but
warns on every agent build. A new settings field holds the OpenAI-compatible endpoint,
defaulting to the AmSC URL, and is passed explicitly through `infer_model`'s
`provider_factory` hook. This is necessary rather than cosmetic: `OPENAI_BASE_URL` is read
from the process environment by the OpenAI SDK, and only `.env.sample` sets it, so without
a file requests would silently go to `api.openai.com`.

*Alternative rejected — a deployment settings table.* With one user, the existing
encrypted user row already serves, and the eviction hook already propagates changes. A new
table would duplicate both.

The dev-mode dummy encryption key is accepted (C4): on a single-user machine the database
file and any generated key are readable by the same account. The dead "Frontier Globus
token" field is removed from the UI only (C5) — nothing reads it (`lib/user_config.py:13-19`
lists the six fields the MCP server sees), but the column, schemas, and encryption test
stay untouched to keep the diff off the backend. Field grouping and placeholder wording
are left alone (C6) as upstream concerns.

### Retrieval: an ungated model, hardcoded in both places (E1–E6)

`microsoft/harrier-oss-v1-270m` — MIT, ungated, 640-dimension, sentence-transformers
native — removes HuggingFace from first run. The name is hardcoded at both existing sites
(`build_rag.py:640` for indexing, `vista_mcp_server/config.py:218` for querying) rather
than unified into one setting.

*Alternative rejected — one shared setting.* Cleaner, but it means new configuration
plumbing across two services in files that are expensive to sync upstream. Hardcoding the
same value in both places is the smallest change that cannot desynchronize; unification is
filed as upstream work. Today's state is worse than either: the indexer never passes the
model through, so the existing environment variable changes only the query encoder and
silently corrupts retrieval.

Weights ship in HuggingFace cache layout and first run copies them into the state
directory (E3), because a filesystem path cannot be hardcoded and `HF_HOME` is
force-assigned in code (`vista_mcp_server/config.py:286`, `backend/config.py:196`) so the
launcher cannot redirect it. `HF_HUB_OFFLINE=1` guards against a silent network reach.

Two accepted defects: no query-side instruction prefix (E4), which the model card says
costs quality — but both call sites already use a bare `encode()`, so this carries an
existing defect forward rather than introducing one; and no dimension-mismatch guard (E5),
since no existing deployment is upgraded. Recorded for future readers: Chroma's default
embedding function would download an ONNX archive from S3, but the download happens inside
`__call__` and every call passes explicit vectors, so it never fires (E6).

### Data: a third seeding branch, keyed on a bundled payload (D1–D5)

`LocalRepoClient` implements the same two-method interface as the GitLab client
(`download_file`, `download_dir`, both repo-relative) and becomes a third `ctx_manager`
branch. Because the four downstream gates are truthiness tests, none of them changes.

*Alternative rejected — a pre-seeded database.* It would need no seeding code at all, but
the knowledge-base row stores absolute paths (`seed.py:264-265`), so a shipped database
would point at the builder's filesystem.

The launcher places the prebuilt store before seeding runs (D2), so the build returns early
instead of embedding 287 MB of PDFs — putting file movement in a new file keeps the
`seed.py` diff to the branch. The PDFs ship too (D3): 287 MB against ~4.5 GB, and
store-only would leave a researcher unable to open a paper the agent just cited. One
assertion is added (D4) because the row hardcodes `build_status="ready"` (`:267`), so a
packaging mistake would present an empty knowledge base as healthy — the only failure here
a researcher could not diagnose. The seeded identity is left as-is (D5).

### Packaging: relocatable environments, verified from elsewhere (P1–P10)

Two things break a copied environment and both are fixed at build time: console scripts
carry absolute shebangs (`uv venv --relocatable`), and `pyvenv.cfg` points `home` at an
interpreter outside the tree (rewritten to the bundled one).

*Alternative rejected — a wheel cache plus install-on-arrival.* Smaller, but it runs an
install step on the recipient's machine, which is the thing being eliminated.

`uv` ships and goes on `PATH` (P2) because it is a runtime dependency, not a build tool:
`agents.py:208` hardcodes `command="uv", args=["run", "dev-mcp-server", ...]` on every
agent session. The launcher exports `UV_NO_SYNC=1` (P3) so `uv run` cannot decide a
relocated environment is stale and attempt a network reinstall mid-session; `agents.py:211`
passes the environment through, so no code change is needed.

Size work is confined to the build: `output: 'standalone'` in the Next config plus a
production build (P4), the MCP-apps build directory excluded in favour of its single
429 KB artifact (P5), a CPU-only torch index for Linux (P6) since the lock otherwise pulls
37 NVIDIA wheels and a CUDA toolkit that a laptop cannot use, and hardlink-preserving
archiving (P7) to collapse ~800 MB duplicated across the two large environments. Gzip is
the default archive format because the recipient must extract before anything of ours runs,
so a missing decompressor cannot be explained; zstd is a build flag. `semgrep` stays (P9) —
removing it means editing the lockfile. `amscrot-py` ships inside the environment (P10),
which is what lets HPC work without giving researchers `amsc2` credentials.

The build preflights its prerequisites and ends by unpacking to a different path depth and
exercising health plus one retrieval query (P8), because relocation is the highest-risk
part of the design and a manifest proves files exist, not that they still run.

### Launch and HPC (L1–L3, H1–H4)

State lives outside the artifact (L1), which makes copying carry no personal data and makes
upgrading a directory replacement. One command performs first-run setup and launch (L2),
with port and platform preconditions reported before services start (L3).

Perlmutter stays in scope (H1): it never touches Globus Transfer — every file operation
goes through the IRI filesystem API (`submit_job_mcp.py:666-689`) and no
`create_globus_client` call site is on its path. Globus setup is gated on whether an OLCF
refresh token is configured **and** made non-fatal, with the transfer service started only
when setup succeeded (H2).

*Alternative rejected — gating alone.* A researcher who exports a refresh token to try
Frontier on a laptop without Docker satisfies the gate and then hits the hard failure the
change exists to remove. Non-fatal handling covers every cause — no runtime, no terminal, a
declined login, no network — and is safe because the MCP server already degrades cleanly:
the collection identifier returns nothing when setup never ran, and the dispatchers check it
first (`submit_job_mcp.py:401-406`, `:722-727`).

`launch.sh` gains a line sourcing the configuration file (H3), matching `build.sh:6`, since
it does not read it today. The NERSC access token's ~48-hour expiry and manual re-entry are
accepted (H4); storing a refresh token instead would change `lib/iri.py`, the most
sync-sensitive file in scope.

## Risks / Trade-offs

- **Relocation silently half-works** → The build's own smoke test unpacks to a different
  path depth and exercises the running artifact, not just its file list.
- **The Next.js standalone output is unmeasured** → No production build exists in the
  reference checkout, so the ~4.5 GB estimate assumes standalone comes in well under the
  493 MB of `node_modules` it replaces. Measure during task group 6 before quoting a size.
- **Retrieval quality is knowingly below the model's capability** (E4) → Uniform across
  indexing and querying, so ranking is self-consistent rather than mismatched; tracked
  separately.
- **The two hardcoded model names can drift** (E2) → A future edit to one silently degrades
  retrieval with no error. Accepted deliberately; unification filed upstream.
- **H2 changes behavior for deployments that need Globus** → The container would boot and
  discover the problem at first submission instead of refusing to start. Judged better than
  today, where it cannot boot at all, but it is a real difference and wants an upstream review.
- **A stale cached collection identifier** → It is a `cached_property`, so starting the
  transfer endpoint after the MCP server leaves the miss cached for the process lifetime.
  Pre-existing; only affects the deferred clusters.
- **Per-platform build matrix** → Each supported platform needs its own build host. The
  artifact carries a platform guard so a mismatch reports itself rather than failing obscurely.

## Migration Plan

No migration. With no bundled-payload path and no `VISTA_SETUP`-style values recorded, the
seeder, builder, and launcher behave as they do today; the container image is unaffected
except that `aws/Dockerfile.server:78` starts working. The embedding-model change applies
to newly built stores only and is not backward compatible with an existing 768-dimension
store — accepted per E5, since this change targets fresh installations. Rollback is
reverting the modified files and deleting the new ones.
