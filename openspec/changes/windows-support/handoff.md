# Handoff: starting the Windows session

For the first session on the Windows x64 machine. Read this, then [tasks.md](tasks.md) from group 6. Delete this file when the change is archived.

## Where things stand (2026-09-23)

- Branch `windows-support`, last commit `d4c96bb`, tree clean. Every `[mac]` task is done and verified: groups 1–5, and 3.2/3.3 on a built package.
- Open, and needing Windows: 4.2 (confirm on a real clone), all of groups 6 and 7.
- Group 8 (close-out) runs last, on either machine. 8.1 removes the interim-store default right before merge.
- Builds made on the Mac passed their smoke tests: `macos-arm64` and `linux-x86_64`. The Linux sandbox has not run live anywhere; the Mac has no KVM.

## Bring from the Mac

| What | Where on the Mac | Used by |
|---|---|---|
| Linux **x64** sandbox image tar | Build it: `docker buildx build --platform linux/amd64 -t vista-sandbox:latest --load mcp_servers/dev_mcp_server/src/dev_mcp_server/docker`, then `docker save -o vista-sandbox-amd64.tar vista-sandbox:latest` | 7.1, `--sandbox-image` |
| Corpus payload | `~/.vista-build/vista-data` | 7.2, `--payload` |
| Vector store | `~/.vista/knowledge-bases/molten-salt-papers/rag_db` | 7.2, `--vector-store` |
| `.env` | the main checkout's `.env` | inference key for 6.9 and the build |

Check the transfers with `shasum -a 256` on both sides (7.1). The image must be linux/amd64; the Mac's own `vista-sandbox:latest` is arm64.

## Credentials the Windows build needs

The same two the Linux build needs (README, "A complete Linux build from a Mac"): `AMSC_GIT_TOKEN` for amsc2 (`amscrot-py`), and `PALISADE_GITHUB_TOKEN` for the backend's `palisade` dependency. On Windows these can be git `url.insteadOf` rules or credentials, as on a native Mac build, rather than exported variables. Ask the user to set them up; don't handle token values yourself.

## Steps the user does

- **6.1 before 6.5.** Record `msb doctor` and the `Win32_OptionalFeature` output while Hypervisor Platform is still **off**. 7.3's detection is built from that output, and turning the feature back off later costs another restart.
- **6.5: enabling Windows Hypervisor Platform.** Needs admin rights and a restart. The restart ends the session, so hand this step to the user with the exact command (`msb doctor --fix`, elevated), and pick up at 6.6 afterwards.
- Installing prerequisites if they are missing: uv, Node, Git for Windows (default settings, which is the point of 4.2/6.2), and Docker Desktop only if the image tar is built on Windows instead of brought over.

## Things to know

- **Sandbox store.** 8.1 removed the interim-store default, so with `MSB_HOME` unset the dev server uses microsandbox's own `%USERPROFILE%\.microsandbox`.
- **Live tests.** `VISTA_RUN_SANDBOX=1 MSB_HOME=%USERPROFILE%\.msb-live uv run pytest -m sandbox` in `mcp_servers/dev_mcp_server` (6.6). They skip unless `MSB_HOME` is set explicitly.
- **Encoding.** Hermetic suites must pass with `PYTHONWARNDEFAULTENCODING=1`; the pytest config turns the warning into an error (6.4). A failure there is a real Windows bug, not noise.
- **Store-path budget.** 51 characters on macOS, measured. Whether Windows has any such limit is open question 6.8. Don't assume the check applies until that is answered.
- **TLS inspection (untested on Windows).** ORNL traffic passes through Netskope. On the Mac this only broke the Linux build container, which has its own trust store. On Windows the root is probably in the system certificate store, but uv and Node may not read it by default. If downloads fail with certificate errors, try `UV_NATIVE_TLS=1` for uv and `NODE_USE_SYSTEM_CA=1` for Node before anything else.
- **Dev stack on Windows.** `launch.sh` is bash, so start the three services by hand with the AGENTS.md commands (6.9).

## Known issues that are not tasks here

- **The dev-mode image digest check never matches** under Docker Desktop's containerd store, so every sandbox spawn re-exports and reloads the image, and concurrent spawns can race on the store cache (`MicrosandboxError: image error: cache error …`). This predates the branch and was split off as a separate task. Packages are unaffected; they run in image-only mode. On Windows this only matters if the dev server runs in Dockerfile mode.
- **Killing `launch.sh`** leaves stopped sandboxes in the store, because `close()` doesn't run. This also predates the branch. Clear them with `msb rm <name>`.

## How to work

- Stop after each task group and report back. Commit only when asked.
- Follow each task's own verification step, and record what it showed in `tasks.md` the way groups 1–5 do.
- Never point a live run at a store another checkout or install uses. Use a throwaway `MSB_HOME` if in doubt.
