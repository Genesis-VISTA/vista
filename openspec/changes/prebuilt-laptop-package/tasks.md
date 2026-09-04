## 1. Boot with no configuration (C1, C2)

- [ ] 1.1 Give `model` the default `openai:claude-sonnet` in `backend/src/vista_backend/config.py:72`, written with the provider prefix; verify `Settings.model_validate({})` succeeds in a shell with no VISTA variables and no `.env` on the path
- [ ] 1.2 Add an OpenAI-compatible endpoint settings field defaulting to the AmSC URL; verify it reads from the environment when set and returns the default when not
- [ ] 1.3 Pass the endpoint and credential explicitly through `infer_model`'s `provider_factory` at `agents.py:373` instead of relying on process environment; verify with a unit test that a constructed agent carries the configured base URL and that `OPENAI_BASE_URL` is not consulted
- [ ] 1.4 Confirm the MCP server and UI need no configuration file: verify `vista-mcp-server` starts and `/mcp` answers with no `.env` present anywhere in the parent path chain
- [ ] 1.5 Add a backend test that the whole settings object constructs with an empty environment, so a future required field is caught immediately; place it alongside `backend/tests/test_crypto.py`

## 2. Inference credentials through the interface (C3, C5)

- [ ] 2.1 Add an encrypted credential column for the inference key using the same `EncryptedStr` pattern as `schemas.py:504`, exposed through the self-update schema; verify a written value is not readable in plaintext via `sqlite3` on the database file
- [ ] 2.2 Resolve the credential at agent-build time — user row first, environment second — and raise a typed error when neither supplies one; verify the resolution order with a unit test covering all four combinations
- [ ] 2.3 Turn that typed error into a response naming the setting and where to enter it, rather than a traceback; verify a chat request with no credential returns the guidance message and a non-5xx status
- [ ] 2.4 Confirm changing the credential takes effect without restart by relying on the existing post-commit eviction (`services/project_agent.py:143-156`); verify a test that a committed user update evicts the pooled agent for that user
- [ ] 2.5 Add the model, endpoint, and credential fields to `UserSettingsModal.tsx`; verify a saved value round-trips and the next message uses it
- [ ] 2.6 Remove the dead "Frontier Globus token" field from `UserSettingsModal.tsx:100,116` and its two entries in `ui/lib/user.ts:30,43`; verify `npm run lint` passes and no other component references it
- [ ] 2.7 Verify that projects, skills, and knowledge bases remain browsable with no inference credential configured — the non-inference features must not be gated

## 3. Ungated embedding model (E1, E2, E3, E6)

- [ ] 3.1 Change the embedding model default to `microsoft/harrier-oss-v1-270m` at `build_rag.py:640` and `mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py:218`; verify both files carry the identical string and add a comment on each naming the other, since they cannot desynchronize silently otherwise
- [ ] 3.2 Confirm the retrieval service starts with the model resolved from `HF_HOME` and no token set; verify with `HF_TOKEN` unset and `HF_HUB_OFFLINE=1` against a primed cache
- [ ] 3.3 Confirm the produced vectors are 640-dimension and that a freshly built store queries correctly end to end; verify by indexing two short documents and asserting a query returns the nearer one
- [ ] 3.4 Record in a comment near the collection calls that Chroma's default embedding function must never be invoked, since it downloads from S3 and every call currently passes explicit vectors; verify no `get_or_create_collection` call site omits `embeddings=` or `query_embeddings=`

## 4. Seeding from a bundled payload (D1, D2, D4)

- [ ] 4.1 Add a bundled-payload path settings field in `backend/src/vista_backend/config.py` alongside `vista_data_token`; verify it defaults to `None` and reads from the environment
- [ ] 4.2 Add `LocalRepoClient` to `backend/src/vista_backend/db/seed.py` implementing `download_file(path, dest)` and `download_dir(path, dest)` against an unpacked payload root, skipping files already on disk like its GitLab counterpart; verify a unit test copies a nested file and a directory tree
- [ ] 4.3 Add the third `ctx_manager` branch at `seed.py:140-148` selecting the local client when the payload path is set, GitLab when the token is set, and the existing no-op otherwise; verify precedence is deterministic when both are set
- [ ] 4.4 Add the non-empty-index assertion before the knowledge-base row is written, so an absent or empty store fails loudly rather than producing a row reporting `build_status="ready"`; verify a test that an empty store raises and names the corpus
- [ ] 4.5 Add a seeding test proving payload-sourced seeding yields the same knowledge-base slug, the same project knowledge-base list, and the same asset-dependent skills as the token path; verify it runs under the default hermetic PR filter
- [ ] 4.6 Confirm `backend/tests/test_seed_projects.py` and `backend/tests/test_seed_splash.py` pass unmodified, proving the tokenless path is untouched; verify via `cd backend && uv run --extra dev pytest -k seed`

## 5. Optional file-transfer setup never blocks startup (H2, H3)

- [ ] 5.1 Add a line sourcing `.env` to `scripts/launch.sh`, matching the `build.sh:6` idiom; verify a value present only in `.env` is visible to the script
- [ ] 5.2 Gate the Globus setup call at `scripts/launch.sh:28` on either OLCF refresh token being non-empty, capture whether it succeeded, and make failure non-fatal with a warning naming the affected clusters; verify a launch on a host with no container runtime and a token set reaches a serving UI
- [ ] 5.3 Start the Globus service only when setup succeeded, in all three launch modes (`:94`, `:104`, `:139`); verify no `globus.log` is produced and no service is started when setup was skipped or failed
- [ ] 5.4 Verify the unset case is unchanged: with both refresh tokens absent, no transfer setup is attempted and every service starts
- [ ] 5.5 Verify the token-set-and-working case is unchanged: on a host where setup succeeds, the endpoint and service start exactly as before
- [ ] 5.6 Verify a dependent job tool reports the incomplete setup by name rather than an internal error — run against the dry-run path so this stays out of PR CI (`hpc` marker)

## 6. UI and build configuration (P4, P5)

- [ ] 6.1 Add `output: 'standalone'` to `ui/next.config.mjs` and confirm it composes with the existing `serverExternalPackages`; verify `npm run build` succeeds and `.next/standalone` contains a runnable server
- [ ] 6.2 Measure the standalone output and record the figure, since the package size estimate depends on it; verify by comparing it against the 493 MB `node_modules` it replaces
- [ ] 6.3 Confirm the standalone server serves the UI and reaches the backend; verify by starting it directly and loading the project list
- [ ] 6.4 Confirm the MCP-apps build emits only `src/vista_mcp_server/mcp-apps/display-file.html` and that its `node_modules` is not needed at runtime; verify the `display_file` tool renders with the build directory's dependencies removed

## 7. Build script (P1, P6, P7, P8, P10, E3, D3)

- [ ] 7.1 Create `build_local_package.sh` with the `scripts/ci-local.sh:35-42` house idiom (`die()`/`log()`, lowercase `error:`); verify `--help` prints usage and an unknown flag exits with the house format
- [ ] 7.2 Preflight the build's own prerequisites — container runtime, `VISTA_DATA_TOKEN`, `amsc2` access, network, and the requested archiver — collecting every failure before exiting non-zero; verify a run missing two prerequisites reports both and builds nothing
- [ ] 7.3 Build the sandbox image and export it as a `docker save` archive; verify `msb load -i <archive> -t vista-sandbox:latest` succeeds on a host with the image store cleared
- [ ] 7.4 Create the virtual environments with `uv venv --relocatable` and rewrite each `pyvenv.cfg` `home` to the bundled interpreter; verify no absolute path outside the package remains in any `pyvenv.cfg` or console-script shebang
- [ ] 7.5 Apply a CPU-only torch resolution for Linux builds, scoped to the packaging build so the committed lock is untouched; verify a Linux build contains no `nvidia-*` distributions and `git status` shows `uv.lock` unmodified
- [ ] 7.6 Fetch the vista-data payload — the molten-salt PDFs, the three MSTDB assets, and the `hpc_jobs/forge-tune` CSV — and build the vector store from the PDFs; verify the store is non-empty and its vectors are 640-dimension
- [ ] 7.7 Stage the embedding weights in HuggingFace cache layout inside the payload; verify the retrieval service loads them with `HF_HUB_OFFLINE=1` from a copy of the payload at a different path
- [ ] 7.8 Assemble the archive as `vista-<version>-<os>-<arch>.tar.gz` with hardlinks and extended attributes preserved, plus a `.sha256`; verify the `msb` binary's code signature survives a round trip through archive and extraction
- [ ] 7.9 Write a manifest of components and sizes alongside the archive; verify it lists every payload element and that a deliberately incomplete build is identifiable from the manifest alone
- [ ] 7.10 Add the post-build smoke test: unpack to a temporary directory at a different path depth, start, poll the three health endpoints, run one retrieval query, shut down; verify the build fails when the smoke test does

## 8. Launcher (L1, L2, L3, P2, P3, D2)

- [ ] 8.1 Create the `vista` launcher resolving its own location and exporting absolute paths for the state directory and the MCP servers directory; verify it runs correctly from any working directory
- [ ] 8.2 Put the bundled `uv` on `PATH` and export `UV_NO_SYNC=1`; verify an agent session spawns the sandbox MCP server with no network access available
- [ ] 8.3 Add the platform guard comparing `uname` against the artifact's build target; verify a deliberately mislabelled artifact refuses to start and names the target platform
- [ ] 8.4 Add the port preflight for 3000, 8000, and 8001 with a message naming the conflicting port; verify startup stops immediately when a port is held rather than waiting on a health poll
- [ ] 8.5 Add first-run detection and setup: import the sandbox image if absent, create the state directory, copy the embedding weights and the prebuilt store into place, then hand off to seeding; verify a second run skips all of it and does not re-import the image
- [ ] 8.6 Report the address to open and write service logs to files rather than interleaving them on stdout; verify a successful start prints one address line
- [ ] 8.7 Stamp a readable version into the artifact and surface it in the launcher output; verify it appears in both the manifest and the running app

## 9. End-to-end verification

- [ ] 9.1 Verify the zero-configuration path on a clean host: unpack, run the launcher, reach a serving UI with no credentials configured, and confirm the chat entry point reports the missing credential by name
- [ ] 9.2 Verify the first-session path: paste an inference credential in the settings modal and confirm the next message succeeds with no restart, and that retrieval answers from the bundled corpus with a citation that opens
- [ ] 9.3 Verify transfer on a second machine of the same platform: copy the archive, unpack, launch, and confirm it starts with none of the sending machine's conversations, uploads, or credentials
- [ ] 9.4 Verify the upgrade path: replace the unpacked artifact with a newer build and confirm existing conversations, uploads, and credentials survive
- [ ] 9.5 Verify no container runtime is required: run the full path on a host with docker and podman absent from `PATH`, including one sandbox `run_bash` call (`sandbox` marker — excluded from PR CI)
- [ ] 9.6 Verify Perlmutter submission from the package with only the three per-user fields entered in the UI (`hpc` marker — excluded from PR CI)
- [ ] 9.7 Verify the no-declaration regression case: on a normal git checkout with no bundled payload path and no refresh tokens recorded, confirm `build.sh` and `launch.sh` execute the same commands as before this change
- [ ] 9.8 Run `./scripts/ci-local.sh` and confirm lint and hermetic tests pass across the backend, ui, and mcp targets
