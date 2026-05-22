# VISTA (Visual Intelligence for Scientific & Tooling Assistant)

## Architecture

- `./mcp_servers`
    - `vista_mcp_server`
        - FastMCP server (HTTP) providing HPC job submission, RAG search, and file display tools. Shared across all agent sessions.
    - `dev_mcp_server`
        - Minimal FastMCP server (STDIO) providing sandbox tools: `run_bash`, `create_file`, `view`. Launched per-agent by the backend; each `ProjectAgent` gets its own instance.
- `./hpc_jobs`
    - Predefined jobs that the agent can submit to the remote HPC system
- `./skills`
    - Agent Skill files. See [docs/skill-onboarding.md](docs/skill-onboarding.md) for the
      SKILL.md schema and how to generate / import / publish skills.
- `./backend`
    - FastAPI backend with the project DB, agent loop, and skill CRUD. See
      [docs/project-onboarding.md](docs/project-onboarding.md) for the project
      schema, the `/projects` CRUD UI, and how a project drives the agent.
- `./ui`
    - Frontend UI and agent loop that calls the tools in the MCP servers

## Prerequisites

- Node.js 20+
- [uv](https://docs.astral.sh/uv/)
- Docker
- [google/embeddinggemma-300m](https://huggingface.co/google/embeddinggemma-300m)
    - VISTA will automatically download the model, but you need to sign up for access to it on [hugging face](https://huggingface.co/google/embeddinggemma-300m)
    - Once authorized, log in using the [hf cli](https://huggingface.co/docs/huggingface_hub/en/guides/cli): `hf auth login` (Or add HF_TOKEN to .env)
- [git lfs](https://git-lfs.com/) (for the rag db)
    - If cloned the repo before installing git lfs, run `git lfs pull` to pull the files

On MacOS, you may need to install `libmagic` first as well:
```bash
brew install libmagic
```

To install the nersc dependencies, you need to be able to ssh to https://gitlab.com/amsc2.
Log into gitlab with your AmSC account [here](https://apps.pingone.com/19636b99-842b-427d-b2f8-754de01a3756/myapps/#), and upload your ssh key.
On ORNL Network, you'll need to set up .ssh/config like so:
```
Host gitlab.com
    User git
    ProxyJump bstn-wks-gate
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
cd ./mcp_servers/vista_mcp_server && uv run vista-mcp-server --transport=http
```

## Jobs
The agent can only submit from a pre-configured list of jobs. These jobs are in the `./hpc_jobs` directory. Each job lives in its own subdirectory and requires at minimum a `job.slurm` script. An optional `s3m_defaults.json` file sets resource defaults for the S3M scheduler.

### Directory layout
```
hpc_jobs/
└── my-job/
    ├── job.slurm          # required — Slurm batch script, run via S3M
    ├── s3m_defaults.json  # optional — resource/duration defaults
    └── README.md          # optional — shown to agent as job description
    └── ...                # Other supporting files. All files will be uploaded to the HPC cluster
```

### job.slurm
A standard Slurm batch script. The agent can pass argument to the job, which you can use in the script.

### s3m_defaults.json
Overrides default S3M submission parameters. All fields are optional:
```json
{
  "duration": 120,
  "resources": {
    "node_count": 1,
    "process_count": null,
    "processes_per_node": null,
    "cpu_cores_per_process": null,
    "gpu_cores_per_process": null,
    "exclusive_node_use": true,
    "memory": null
  }
}
```
`duration` is in **seconds**. `memory` is in **bytes**. If `s3m_defaults.json` is absent, the defaults are 120 s and 1 node.

### Job Output
Inside the job, the `VISTA_OUT` environment variable will be set to the path of an output directory. Any output files and logs should be saved
under that directory so that Vista can pull the results.
