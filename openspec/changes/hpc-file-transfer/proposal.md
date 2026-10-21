## Why

Issue #83 (Genesis Hackathon feedback, "VISTA Next Steps" slide): Skill Hub: "File transfer
skill for moving data".

An agent moves files to or from Odo and Frontier only as part of a job: `submit_hpc_job`
uploads the job's sources, and `get_hpc_job_outputs` downloads from that job's output folder.
It cannot look at a dataset in a project area, fetch a file no VISTA job produced, or put an
input on the cluster for a job to read, although `lib/globus.py` already does all three.

A VISTA skill only tells the agent how to use tools, and the sandbox holds no Globus
credentials, so a skill alone would have nothing to call. The request is delivered as tools,
with the guidance in their descriptions. Every project has them, and no skill needs attaching.

## What Changes

- Three new MCP tools for Odo and Frontier, each acting as the researcher's own Globus identity:
  - **list** the entries under any path the researcher can read;
  - **download** files from any path the researcher can read into the project's output folder,
    where the sandbox and `display_file` see them;
  - **upload** files from the project's output or uploads folder into a `transfers/` folder
    inside the researcher's own sources folder on that cluster, and nowhere else, reporting
    the remote path a job can read.
- The tools accept only `odo` or `frontier` as the cluster.
- Downloads have no size cap, as with job outputs today. The tool descriptions say to leave
  bulk data on the cluster.
- An upload replaces a file of the same name in `transfers/`.
- An expired Globus session is reported as expired, never as a missing or empty folder.
- `get_hpc_job_outputs` behaves as before and shares its download code with the new tool.

## Non-goals

- Cluster-to-cluster moves through Globus Transfer tasks: the collections are High Assurance in
  different OLCF enclaves, and whether OLCF permits it is unasked.
- Perlmutter (IRI download is text-only) and Lux (SFTP).
- A `SKILL.md`, MCP server `instructions`, or a UI change.
- A download size cap, or approval before an upload.
- VISTAGuard.

## Capabilities

### New Capabilities

- `hpc-file-transfer`: listing, downloading and uploading files on Odo and Frontier outside a
  job: read and write scope, local destinations, and failure reports.

### Modified Capabilities

- `hpc-job-submission`: "One folder layout" gains the `transfers/` folder beside the job
  sources. "Expired Globus session on file operations" names the transfer tools.

## Impact

- New `mcp_servers/vista_mcp_server/src/vista_mcp_server/transfer_mcp.py`, mounted in
  `server.py` unless `VISTA_MCP_DISABLE_SERVERS` names `transfer`.
- `submit_job_mcp.py`: the download loop in `_get_olcf_job_outputs` becomes a shared helper,
  and `_job_output_paths` is generalised for both callers.
- `lib/globus.py`: unchanged; listing, `stat`, download, upload and `mkdir -p` are reused.
- `backend/src/vista_backend/agents/agents.py`: the three tool names join `HPC_TOOLS`, the
  list of tools the researcher's credentials are sent to.
- Tests: new `mcp_servers/vista_mcp_server/tests/test_transfer_tools.py` with a fake Globus
  client; existing output-retrieval tests keep passing.
- Docs: `docs/hpc.md` and the Globus section of `AGENTS.md`.
- No new dependency.
