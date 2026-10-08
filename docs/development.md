# Developing VISTA

Running VISTA from a checkout: prerequisites, configuration, and the ways to start the three
services. For an installed package, see [installing.md](installing.md).

A checkout runs three services and a window:

| Service | Directory | Port | Started by hand with |
|---|---|---|---|
| MCP server (HPC, RAG and `display_file` tools) | `mcp_servers/vista_mcp_server` | 8000 | `uv run vista-mcp-server --transport=http` |
| Backend (FastAPI + PydanticAI: the agent loop, projects, skills) | `backend` | 8001 | `uv run vista-backend` |
| UI (Next.js, a thin proxy to the backend) | `ui` | 3000 | `npm run dev` |
| Window (Electron) | `electron` | — | `./launch.sh` starts it |

The sandbox tools (`dev_mcp_server`: `run_bash`, `create_file`, `view`) are not a service: the
backend launches one per agent over STDIO.

## Prerequisites

- Node.js 20.9 or later
- [uv](https://docs.astral.sh/uv/). The Python services need Python 3.14, which uv fetches
  for you.
- git 2.34 or later, for the Hypothesis Lab (the rest of VISTA works without it)
- Docker or Podman, for the agent's code-execution sandbox image, which a checkout builds on
  first launch. A prebuilt package ships the image already built and needs neither.
- Network access to GitHub, where `uv sync` in `backend/` fetches [PALISADE](palisade.md) at a
  pinned tag. The repository is public, so no GitHub credentials are needed; its licence is
  still a placeholder (see [PALISADE](palisade.md#licence)).
- Read access to the amsc2 GitLab group, for the private `amscrot-py` that HPC job submission
  needs (see [below](#hpc-dependencies)).

The embedding model,
[microsoft/harrier-oss-v1-270m](https://huggingface.co/microsoft/harrier-oss-v1-270m), is
downloaded automatically. It is MIT-licensed and ungated, so no HuggingFace account, terms
acceptance, or `HF_TOKEN` is needed.

Globus Connect Personal is **not** a prerequisite on any platform, and VISTA does not run a
Globus endpoint of its own; see [hpc.md](hpc.md).

### HPC dependencies

The NERSC/OLCF IRI SDK (`amscrot-py`) is in the vista MCP server's optional `hpc` extra, and
needs access to
<https://gitlab.com/amsc2/infrastructure-and-services/infrastructure-services/resource-orchestration/amsc-isro-toolkit.git>.
`./scripts/build.sh` already includes it; by hand:

```bash
cd mcp_servers/vista_mcp_server && uv sync --extra hpc
```

If you cloned VISTA over SSH you may need:

```bash
git config --global url."ssh://git@gitlab.com/amsc2/".insteadOf "https://gitlab.com/amsc2/"
```

Set `VISTA_MCP_DISABLE_SERVERS=submit_job` to skip mounting the job tools entirely.

## Configuration

Copy the sample env file and fill it in:

```bash
cp .env.sample .env
```

`.env.sample` documents each setting. The backend, the MCP server, `./launch.sh` and
`./scripts/build.sh` all read the repo-root `.env`; a value already exported in your
environment wins over the file.

| Variable | Description | Default |
| --- | --- | --- |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `VISTA_BACKEND_MODEL` | The installation's default inference endpoint and model. A provider and key chosen in the UI, under Settings › Agent, always win. Get an AmSC key from <https://api.i2-core.american-science-cloud.org>. | AmSC i2, `openai:claude-sonnet` |
| `VISTA_MCP_OMD_API_KEY` | Key for the OpenMetaData catalog. Also uses the AmSC inference API key | None |
| `VISTA_DATA_TOKEN` | A code.ornl.gov token for `v28/vista-data`, the private corpora | None |
| `VISTA_BACKEND_FORUM__ENABLED` | The Hypothesis Lab; see [hypothesis-forum-hosting.md](hypothesis-forum-hosting.md) | `false` in a checkout |
| `VISTA_BACKEND_PALISADE__*` | The security sidecar; see [palisade.md](palisade.md) | off |

Per-user HPC credentials are not env vars: each researcher connects them in Settings. See
[hpc.md](hpc.md).

## Starting VISTA

```bash
./launch.sh logs
```

This builds what is missing, starts the MCP server, backend and UI, logging to `logs/mcp.log`,
`logs/backend.log` and `logs/ui.log`, and opens the UI in the VISTA window. Closing the window,
or Ctrl-C, stops everything. The first launch can take a few minutes while the MCP server builds
the sandbox image. `logs` is the default mode, so plain `./launch.sh` does the same.

The window is installed by `./scripts/build.sh --electron` (a ~290 MB Electron download that a
plain `./scripts/build.sh` skips), which `./launch.sh` runs for you, and it opens
`http://localhost:3000` once the UI answers. Hot reload works as in a browser, DevTools are in
the View menu. From the macOS Dock and app switcher the window reads "Electron" in development;
only the packaged build is named VISTA. On Linux the development window makes the same sandbox
check as the package, and the same AppArmor profile turns the sandbox on for it:

```bash
sudo install -m 644 electron/linux/vista-window.apparmor /etc/apparmor.d/vista-window
sudo apparmor_parser -r /etc/apparmor.d/vista-window
```

The window's code and tests are in [`electron/`](../electron/).

### Without the window

Over SSH, or anywhere without a display, the window cannot open and `./launch.sh` says so
rather than falling back. Start the services alone and use a browser at <http://localhost:3000>:

```bash
./launch.sh logs --no-electron
```

`./launch.sh terminal` starts the three services in separate terminal windows instead, and
`./launch.sh tmux` in a tmux session. Neither opens the VISTA window or owns the services'
lifetime, so stop each service yourself.

### By hand

Build once, then start each service in its own terminal:

```bash
./scripts/build.sh
```

```bash
cd mcp_servers/vista_mcp_server && uv run vista-mcp-server --transport=http
```

```bash
cd backend && uv run vista-backend
```

```bash
cd ui && npm run dev
```

Then open <http://localhost:3000>. `curl http://localhost:3000/api/mcp/health` is a quick smoke
test.

## The thin macOS developer app

On a Mac, build a local application backed by the current checkout:

```bash
./scripts/build_mac_dev_app.sh
open "dist/mac-dev/VISTA Dev.app"
```

The first build installs only missing checkout dependencies; later builds normally package and
ad-hoc-sign the Electron application in seconds. Pass `--refresh-dependencies` after lockfile
changes. No Apple account, corpus, vector store, exported sandbox image, bundled runtime, or
access to the private `amscrot-py` repository is required.

`VISTA Dev.app` shows the same preparation window as the distributable, then starts the MCP
server, backend, and Next.js UI directly from this checkout. It uses `~/.vista-dev`, the bundle
identifier `gov.ornl.vista.dev`, and disables live HPC job submission by default. Other local
features remain available; code-execution still needs the normal local container/sandbox setup
when used. Closing the app stops all three source services. Logs are in
`~/.vista-dev/logs/dev-stack.log` and the checkout's `logs/` directory.

The generated app records the checkout's absolute path in `dist/mac-dev/dev-root`, so it is a
local developer artifact, not something to send to another machine. Each developer builds their
own copy. Use `build_local_package.sh` only for a complete, relocatable release package; see
[building-packages.md](building-packages.md).

## Testing

`./scripts/ci-local.sh` mirrors GitLab CI locally: every lint and hermetic test, or a target
(`backend`, `ui`, `mcp`, `electron`, `install`, `launcher`, `docs`) and an action (`lint`,
`test`). `./scripts/ci-local.sh install-hooks` makes the git pre-commit hook run
`lint --fast`. Live, HPC and Playwright checks run on a schedule or by hand; see
[validation-lane.md](validation-lane.md). The testing roadmap's requirements are in
[`openspec/specs/`](../openspec/specs/).
