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
| Collaboration server (optional, development only) | `collaboration_server` | 8002 | `uv run vista-collaboration-server` |

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

The embedding model,
[microsoft/harrier-oss-v1-270m](https://huggingface.co/microsoft/harrier-oss-v1-270m), is
downloaded automatically. It is MIT-licensed and ungated, so no HuggingFace account, terms
acceptance, or `HF_TOKEN` is needed.

Globus Connect Personal is **not** a prerequisite on any platform, and VISTA does not run a
Globus endpoint of its own; see [hpc.md](hpc.md).

### HPC job submission

Job submission needs no private dependency: VISTA talks to each cluster's IRI service with its
own client (`mcp_servers/vista_mcp_server/src/vista_mcp_server/lib/iri.py`), and researchers
supply their tokens in Settings. See [hpc.md](hpc.md).

Set `VISTA_MCP_DISABLE_SERVERS=submit_job` to skip mounting the job tools entirely.

## Configuration

Copy the sample env file and fill it in:

```bash
cp .env.sample .env
```

`.env.sample` documents each setting. The backend, the MCP server, `./launch.sh` and
`./scripts/build.sh` all read the repo-root `.env`, but they disagree about a value that is also
exported in your environment: the backend and the MCP server keep the exported value, while
`./launch.sh` and `./scripts/build.sh` load the file over it. So to override a setting for one
run of `./launch.sh`, change `.env` rather than exporting it.

| Variable | Description | Default |
| --- | --- | --- |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `VISTA_BACKEND_MODEL` | The installation's default inference endpoint and model. A provider and key chosen in the UI, under Settings › Models & providers, always win. `OPENAI_API_KEY` is an AmSC MAG project access token, from <https://my.amsc.energy.gov/>. | AmSC MAG, `openai:claude-sonnet` |
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
or Ctrl-C, stops everything. Each start deletes `logs/` first, so copy anything you want to keep
out of it. The first launch can take a few minutes while the MCP server builds the sandbox image.
`logs` is the default mode, so plain `./launch.sh` does the same.

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

### With a collaboration server

```bash
./launch.sh logs --collab
```

also starts a local collaboration server, logging to `logs/collab.log`, and points the backend
at it with `VISTA_BACKEND_COLLABORATION_SERVER_URL`; an address set under Settings →
Collaboration takes its place. `--collab` works in every mode. The server keeps its data in
`data/collaboration-server/` (`VISTA_COLLAB_DATA_DIR`), listens on `127.0.0.1:8002`
(`VISTA_COLLAB_HOST`, `VISTA_COLLAB_PORT`), and runs in `dev-headers` mode: it believes the
caller's identity headers as sent, so it refuses to listen on anything but loopback. Packages
never start one. How the server works, and how a deployment signs researchers in, is in
[The collaboration server](collaboration-server.md).

Without `--collab`, a dev stack uses VISTA's default server on Obsidian, like an installed
VISTA: it checks its status whenever a page asks, a few seconds at most and never failing. To
keep a stack off it, put `VISTA_BACKEND_COLLABORATION_SERVER_URL=` in `.env`, or turn
collaboration off under Settings → Collaboration. Backend tests never reach it: `conftest.py`
sets the configured address to none for every test.

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
changes. No Apple account, corpus, vector store, exported sandbox image or bundled runtime is
required.

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

## The docs site

The pages of `docs/` listed in `docs/site.toml` are published at
<https://genesis-vista.github.io/docs/>, built from VISTA's latest release. A docs change
shows up there with the next release. `./scripts/ci-local.sh docs` checks the links, the
manifest and a strict build of the published pages.

To preview the site locally, from the repo root:

```bash
git clone --depth 1 https://github.com/Genesis-VISTA/genesis-vista.github.io ../vista-site
uv run --no-project --python 3.14 --with mkdocs-material==9.8.0 python -I scripts/docs_site/sync_docs.py . ../vista-site --ref local
uv run --no-project --python 3.14 --with mkdocs-material==9.8.0 python -I -m mkdocs serve -f ../vista-site/mkdocs.generated.yml
```

The Material version matches `MKDOCS_MATERIAL_VERSION` in `.gitlab-ci.yml`. Links between
published pages must be relative links that `docs:links` accepts.
