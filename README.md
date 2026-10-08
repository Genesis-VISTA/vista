# VISTA

**Visual Intelligence for Scientific & Tooling Assistant.** VISTA is an agentic scientific assistant. It is a desktop
application in which an AI agent searches the literature, runs analysis code in a sandbox, and
submits simulations to configured HPC clusters (currently supports OLCF and NERSC).

## Getting started

On **macOS** (Apple Silicon) or **Linux** (x86-64), in a terminal:

```bash
curl -fsSL https://github.com/Genesis-VISTA/vista/releases/latest/download/install.sh | bash
```

On **Windows** (x64), in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://github.com/Genesis-VISTA/vista/releases/latest/download/install.ps1 | iex"
```

To build and run from a checkout instead, see [run from source](#run-from-source).

## After installing

The installer checks the package against its checksum, installs it and opens VISTA. Then:

1. Paste your inference API key into **Settings › Agent** (Settings is at the bottom of the
   sidebar), and pick a model at the top of the chat.
2. To submit HPC jobs, connect each cluster in Settings; see [HPC clusters](docs/hpc.md).
3. Next time, open VISTA like any other application: `VISTA.app` on macOS, the app menu on
   Linux, the Start menu on Windows.
4. To upgrade, run the installer again with VISTA closed; your state is kept.

VISTA is a desktop application with no browser mode, so it needs a graphical session and
refuses to start over SSH or without a display. On Linux it also needs `/dev/kvm`. Where VISTA
is installed, where it keeps its state, first-launch behaviour and Linux troubleshooting are in
[installing VISTA](docs/installing.md).

## What it does

- **Chat with a scientific agent** backed by the inference endpoint you choose (AmSC i2, AmSC
  MAG, OLCF Inference or your own).
- **Projects** scope what the agent knows and can do: its system prompt, skills, tools and
  usage limits. See [project onboarding](docs/project-onboarding.md).
- **Knowledge bases**: retrieval over indexed paper collections, with citations bound to the
  passages the agent read.
- **Skills**: reusable instructions and scripts, written by hand, generated from a chat or
  imported from GitHub, and shared through the Skill Hub. See
  [skill onboarding](docs/skill-onboarding.md).
- **Sandboxed code execution**: each agent session gets its own microVM for `run_bash` and file
  work, so analysis code never runs on your machine directly.
- **HPC jobs** on Odo, Frontier and Lux (OLCF) and Perlmutter (NERSC), from a catalog of
  predefined jobs, using each researcher's own credentials. See [HPC clusters](docs/hpc.md).
- **Hypothesis Lab**: multi-agent debates over a project's hypotheses, kept in a plain git
  repository so other people and installs can take part. See
  [hosting a forum](docs/hypothesis-forum-hosting.md).
- **Campaigns**: a planner agent that delegates to subagents. See the
  [multi-agent framework](docs/multi-agent-framework.md).
- **PALISADE**: an optional security sidecar that gates prompts, tool calls, retrievals, code,
  jobs and citations. See [PALISADE](docs/palisade.md).

## Status

VISTA is under active development and pre-1.0: interfaces and settings change between
releases. Release packages are published on the
[GitHub mirror](https://github.com/Genesis-VISTA/vista/releases); the canonical repository is on
GitLab.

## Run from source

You need Node.js 20.9+, [uv](https://docs.astral.sh/uv/), Docker or Podman, and read access to
the private `amscrot-py` on the amsc2 GitLab, which HPC job submission needs. Details are in
[developing VISTA](docs/development.md#prerequisites).

```bash
cp .env.sample .env              # then fill in your keys
./launch.sh logs                 # build, start every service, open the VISTA window
./launch.sh logs --no-electron   # services only, headless, for a browser at http://localhost:3000
```

`./launch.sh` logs to `logs/mcp.log`, `logs/backend.log` and `logs/ui.log`, and stops
everything when the window closes or on Ctrl-C. Other ways to start the services, and the thin
macOS developer app, are in [developing VISTA](docs/development.md).

## Architecture

```
  VISTA window (Electron)
          │
  UI (Next.js, :3000) ── thin proxy; the browser never talks to the agent or MCP directly
          │
  Backend (FastAPI + PydanticAI, :8001) ── agent loop, projects, skills, knowledge bases,
          │                                 Hypothesis Lab, PALISADE gates; SQLite database
          ├── vista MCP server (:8000) ── RAG search, HPC jobs, display_file
          └── dev MCP server (STDIO, one per agent) ── run_bash, create_file, view in the sandbox
```

| Directory | What it is |
|---|---|
| [`backend/`](backend/) | FastAPI service that owns the agent loop, the project database, skills and MCP tool dispatch |
| [`ui/`](ui/) | Next.js App Router frontend; its `app/api/*` routes proxy to the backend |
| [`electron/`](electron/) | The VISTA window and, in a package, the application that starts and supervises the services |
| [`mcp_servers/vista_mcp_server/`](mcp_servers/vista_mcp_server/) | MCP server with the RAG, HPC and `display_file` tools |
| [`mcp_servers/dev_mcp_server/`](mcp_servers/dev_mcp_server/) | Sandbox tools, launched by the backend over STDIO for each agent |
| [`hpc_jobs/`](hpc_jobs/) | The predefined jobs the agent can submit; see [HPC jobs](docs/hpc-jobs.md) |
| [`backend/src/vista_backend/db/skills/`](backend/src/vista_backend/db/skills/) | The skills VISTA seeds into its default projects (`SKILL.md`) |
| [`palisade_contracts/`](palisade_contracts/) | VISTA's domain contracts and policies for PALISADE |
| [`scripts/`](scripts/) | Build, launch, packaging, install and local-CI scripts |
| [`openspec/`](openspec/) | Specs and change proposals |

## Testing

```bash
./scripts/ci-local.sh            # every lint and hermetic test, as GitLab CI runs them
./scripts/ci-local.sh backend test   # or one target and action
```

Targets are `backend`, `ui`, `mcp`, `electron`, `install`, `launcher` and `docs`. Live, HPC
and browser checks run on a schedule or by hand; see the [validation lane](docs/validation-lane.md).
Requirements live in [`openspec/specs/`](openspec/specs/).

## Documentation

| Guide | For |
|---|---|
| [Installing VISTA](docs/installing.md) | Installing, first launch, upgrades, state, Linux troubleshooting |
| [Developing VISTA](docs/development.md) | Prerequisites, configuration, running from source, headless development |
| [HPC clusters](docs/hpc.md) | Cluster credentials, remote directories, how jobs run |
| [HPC jobs](docs/hpc-jobs.md) | Adding and maintaining the predefined jobs |
| [PALISADE](docs/palisade.md) | The security sidecar and how VISTA configures it |
| [Building packages](docs/building-packages.md) | Local package builds, build inputs, flags, smoke test |
| [Releasing](docs/releasing.md) | Release automation, verification, publishing, rollback |
| [Project onboarding](docs/project-onboarding.md) | Projects and how they drive the agent |
| [Skill onboarding](docs/skill-onboarding.md) | Writing, generating, importing and publishing skills |
| [Hypothesis Lab hosting](docs/hypothesis-forum-hosting.md), [forum format](docs/forum-git-format.md) | The git-backed debate forum |
| [Multi-agent framework](docs/multi-agent-framework.md), [SPLASH playbook](docs/splash-planner-playbook.md) | Planner and subagent campaigns |
| [API example](docs/api-example-alloy-tc.md) | Driving an HPC simulation through the API |
| [Validation lane](docs/validation-lane.md), [evaluation runbook](docs/evaluation-runbook.md) | Live validation and metrics collection |
| [`AGENTS.md`](AGENTS.md) | Notes for AI coding agents working in this repository |

## Licence

This repository does not yet carry a licence file. PALISADE, which the backend depends on, is
publicly visible on GitHub, but its own licence is still a placeholder pending ORNL software-release
review: as of v1.0.0 it grants no rights to use, copy, modify or distribute it. Ask the
maintainers about terms of use and redistribution.
