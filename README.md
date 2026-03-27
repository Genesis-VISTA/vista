# VISTA (Visual Intelligence for Scientific & Tooling Assistant)

## Architecture

- `./mcp-server`
    - A MCP Server containing tools for sandboxed code execution, remote HPC job submission, and other tasks
- `./hpc_jobs`
    - Predefined jobs that the agent can submit to the remote HPC system
- `./skills`
    - Agent Skill files
- `./ui`
    - Frontend UI and agent loop that calls the tools in the mcp-server

## Prerequisites

- Python 3.12+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/)
- Docker (for sandboxed code execution)

## Environment Setup

Copy the sample env file:
```bash
cp .env.sample .env
```
and fill out your env keys and settings.

At least set:
- `OPENAI_API_KEY` with your AmSC inference API key (get from https://api.i2-core.american-science-cloud.org)
- `VISTA_MCP_OMD_API_KEY` this also uses the AmSC inference API key
- `VISTA_MCP_REMOTE_HPC_JOBS_DIR` where to upload HPC jobs, e.g. `/ccs/home/<username>/vista`

## Launch
The launch script will build all dependencies and launch both the MCP server and the frontend in a tmux session.
```bash
./launch.sh
```
Wait for both to be ready (the MCP server can take a few minutes the first launch as it will build the sandbox Docker image).
Then go to https://localhost:3000

You can use
```bash
./launch.sh terminal
```
to bring up the MCP server and frontend in terminal windows instead of a tmux session.

### Manual launch
Run:
```bash
./build.sh
```

Then launch in separate terminals run:
```bash
cd ./ui && npm run dev
```

```bash
cd ./mcp-server && uv run --env-file ../.env vista-mcp-server --transport=http
```
