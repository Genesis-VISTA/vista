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

- Node.js 20+
- [uv](https://docs.astral.sh/uv/)
- Docker
- [google/embeddinggemma-300m](https://huggingface.co/google/embeddinggemma-300m)
    - VISTA will automatically download the model, but you need to sign up for access to it on [hugging face](https://huggingface.co/google/embeddinggemma-300m)
    - Once authorized, log in using the [hf cli](https://huggingface.co/docs/huggingface_hub/en/guides/cli): `hf auth login`
- [git lfs](https://git-lfs.com/) (for the rag db)
    - If cloned the repo before installing git lfs, run `git lfs pull` to pull the files

On MacOS, you may need to install `libmagic` first as well:
```bash
brew install libmagic
```

## Environment Setup

Copy the sample env file:
```bash
cp .env.sample .env
```
and fill out your env keys and settings.

Important env vars:
| Variable                      | Description                                                                                      | Default                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------- |
| OPENAI_API_KEY                | Your AmSC inference API key (get from https://api.i2-core.american-science-cloud.org)            | None (required)                           |
| VISTA_MCP_S3M_TOKEN           | See [s3m docs](https://docs.olcf.ornl.gov/services_and_applications/s3m/overview.html#get-a-token). Use open enclave and the gen150-vista project. Token expires in 24 hours. | None (required) |
| VISTA_MCP_HPC_SSH_USER        | SSH username to log into Odo (ucams id)                                                          | None (required)                           |
| VISTA_MCP_REMOTE_HPC_JOBS_DIR | Where to upload HPC jobs                                                                         | /gpfs/wolf2/olcf/gen150/proj-shared/vista |
| VISTA_MCP_OMD_API_KEY         | Key for the OpenMetaData catalog. Also uses the AmSC inference API key                           | None (optional)                           |

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
