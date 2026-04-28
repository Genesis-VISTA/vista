"""
MCP for remote HPC job submission via S3M API.
"""

from __future__ import annotations
import itertools, json, logging, os, shlex, textwrap, dataclasses
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP, Context

from .config import settings
from .lib.ssh import Confirmation
from .lib import s3m
from .lib.s3m import S3mDefaults, create_s3m_client, get_s3m_client
from .lib.misc import parse_time_limit, validate_job_id, get_tool_call_string


@dataclasses.dataclass
class JobInfo:
    name: str
    description: str
    s3m_defaults: S3mDefaults


def get_available_jobs() -> dict[str, JobInfo]:
    jobs = {}
    for file in settings.local_hpc_jobs_dir.iterdir():
        if file.is_dir():
            job_script = file / "job.slurm"
            if not job_script.exists():
                raise ValueError(f"Job {file} missing job.slurm")
            readme = file / "README.md"
            if not readme.exists():
                raise ValueError(f"No README.md in {file}")
            description = readme.read_text().strip()
            if not description.startswith(f"# {file.name}"):
                raise ValueError(f'Job README.md should start with "# {file.name}" header')
            s3m_defaults_file = file / "s3m_defaults.json"
            if s3m_defaults_file.exists():
                s3m_defaults = S3mDefaults.model_validate_json(s3m_defaults_file.read_text())
            else:
                s3m_defaults = S3mDefaults()
            jobs[file.name] = JobInfo(
                name=file.name,
                description=description,
                s3m_defaults=s3m_defaults,
            )
    return jobs


AVAILABLE_JOBS = get_available_jobs()


def build_job_descriptions() -> str:
    return '\n\n\n'.join(info.description for name, info in AVAILABLE_JOBS.items())


MAX_NODES = 64
MAX_TIME = int(parse_time_limit("4:00:00").total_seconds())


@asynccontextmanager
async def lifespan(server):
    s3m._s3m_client = await create_s3m_client()
    try:
        yield
    finally:
        s3m._s3m_client.ssh_conn.close()
        s3m._s3m_client = None

mcp = FastMCP("Submit Job", lifespan=lifespan)


@mcp.tool(
    description=textwrap.dedent(f"""
        Submit a job to the HPC system.

        Args:
            job: The name of the job to run (available jobs: {' '.join(AVAILABLE_JOBS.keys())})
            node_count: Number of nodes for the job (max: {MAX_NODES})
            duration: Time limit for the job in "h:mm:ss" format (max: {MAX_TIME})
            script_args: Extra arguments to pass to the script

        Returns:
            The job id.

        Available Jobs:

        {textwrap.indent(build_job_descriptions(), '        ').strip()}
    """).strip(),
)
async def submit_hpc_job(
    ctx: Context,
    job: str,
    node_count: int | None = None,
    duration: str | None = None,
    script_args: str | None = None,
) -> str:
    if job not in AVAILABLE_JOBS:
        raise ValueError(f"{job} is not recognized, should be one of: {' '.join(AVAILABLE_JOBS)}")
    if node_count and (node_count > MAX_NODES or node_count <= 0):
        raise ValueError(f"node_count out of range (max {MAX_NODES})")
    duration_int = int(parse_time_limit(duration).total_seconds()) if duration else None
    if duration_int and (duration_int > MAX_TIME or duration_int < 1):
        raise ValueError(f"Time limit too large (max: {MAX_TIME})")

    # TODO: Move confirm logic to the client side. MCP elicitation is not the right place for this,
    # but we're using it here for ease of migration.
    confirm_result = await ctx.elicit(
        message=f"Confirm running on {settings.hpc_ssh_host[-1]}:\n" +
            get_tool_call_string('submit_hpc_job', job=job, node_count=node_count, duration=duration, script_args=script_args),
        response_type=Confirmation,
    )
    if confirm_result.action != "accept" or not confirm_result.data.confirm:
        raise Exception("Job submission cancelled by user")

    job_info = AVAILABLE_JOBS[job]

    remote_job_dir = settings.remote_hpc_jobs_dir / settings.session_id / job
    s3m_client = get_s3m_client()
    check_result = await s3m_client.bash(f'[ -d {shlex.quote(str(remote_job_dir))} ] && echo true || echo false')
    if check_result.strip() != "true":
        await s3m_client.bash(f'mkdir -p -m 2775 {shlex.quote(str(remote_job_dir.parent))}')
        await s3m_client.upload(settings.local_hpc_jobs_dir / job, remote_job_dir)
        await s3m_client.bash(f'chmod -R g+rwX {shlex.quote(str(remote_job_dir))}')
        logging.info(f"Synced job to {remote_job_dir}")
        # TODO: should clean up the job script eventually, but don't want to do it on close as we may
        # want to leave jobs running between sessions.

    remote_job_script = remote_job_dir / "job.slurm"
    job_cmd = "\n".join([
        f"{settings.get_hpc_setup_script()}",
        f"source {shlex.quote(str(remote_job_script))} {shlex.join(shlex.split(script_args or ''))}",
    ])

    resources_overrides = {
        "node_count": node_count,
    }
    spec = {
        "executable": "/bin/bash",
        "arguments": ["-c", job_cmd],
        "name": f"vista-{job}",
        "directory": str(remote_job_dir),
        "stdout_path": f"{settings.remote_hpc_jobs_dir}/out/%j/log.out",
        "stderr_path": f"{settings.remote_hpc_jobs_dir}/out/%j/log.out",
        "environment": {},
        "resources": {
            **job_info.s3m_defaults.resources.model_dump(mode="json", exclude_none=True),
            **{k: v for k, v in resources_overrides.items() if v is not None},
        },
        "attributes": {
            "account": settings.hpc_account,
            "queue_name": "batch",
            "duration": job_info.s3m_defaults.duration if duration_int is None else duration_int,
        },
    }
    response = await s3m_client.submit_job(spec)
    job_id = str(response.get("id") or "")
    if not job_id:
        raise ValueError(f"Failed to get job ID from S3M response: {response}")
    logging.info(f"Submitted job {job_id} via S3M to odo")
    return job_id


@mcp.tool()
async def get_hpc_job_status(job_id: str) -> str:
    """
    Get the status and logs of a submitted HPC job.

    Args:
        job_id: The job id returned by submit_hpc_job
    """
    job_id = validate_job_id(job_id)

    s3m_client = get_s3m_client()
    job_data = await s3m_client.get_job_status(job_id)
    job_name = job_data.get("status", {}).get("meta_data", {}).get("s3m", {}).get("name", "")
    if not job_name.startswith("vista-"):
        raise ValueError(f"No vista job {job_id!r} found")

    metadata = {
        "JOB_ID": job_id,
        "STATE": job_data.get("status", {}).get("state", "UNKNOWN").upper(),
    }

    # Fetch logs and output file listing
    output_dir = settings.remote_hpc_jobs_dir / "out" / job_id
    log_path = output_dir / "log.out"
    logs = await s3m_client.bash(f"cat {shlex.quote(str(log_path))} 2>/dev/null || true")

    excludes = ['**/.venv*/*', '**/__pycache__/*']
    find_result = await s3m_client.bash(shlex.join([
        "find", str(output_dir),
        "-maxdepth", "3",
        "-type", "f",
        *itertools.chain(*[["-not", "-path", e] for e in excludes]),
    ]) + " 2>/dev/null")
    files = [
        str(Path(line.strip()).relative_to(output_dir))
        for line in find_result.strip().splitlines()
    ]
    files = files[:20]

    return "\n\n".join([
        "\n".join(f"{k}={v}" for k, v in metadata.items()),
        "--- LOGS ---",
        logs.strip() if logs.strip() else "(no logs yet)",
        "--- OUTPUT FILES ---",
        "\n".join(files) if files else "(no output files yet)",
    ])


@mcp.tool()
async def get_hpc_job_outputs(ctx: Context, job_id: str, files: list[str]) -> str:
    """
    Download output files from an HPC job.

    Args:
        job_id: The job id returned by submit_hpc_job
        files: List of file paths to download. Relative to the jobs output directory (as shown by get_hpc_job_status).

    Returns:
        The downloaded file paths.
    """
    job_id = validate_job_id(job_id)

    remote_out_dir = settings.remote_hpc_jobs_dir / "out" / job_id
    local_out_dir = settings.output_dir / job_id
    sandbox_out_dir = Path("/mnt/data/output") / job_id

    downloaded = []
    for file in files:
        remote_path = Path(os.path.normpath(remote_out_dir / file))
        if not remote_path.is_relative_to(remote_out_dir):
            raise ValueError(f'Invalid path "{file}", must be under job output dir')
        local_path = local_out_dir / remote_path.relative_to(remote_out_dir)
        sandbox_path = sandbox_out_dir / remote_path.relative_to(remote_out_dir)
        
        local_path.parent.mkdir(parents=True, exist_ok=True)
        await get_s3m_client().download(remote_path, local_path)
        downloaded.append(str(sandbox_path))

    return "Downloaded files:\n" + "\n".join(downloaded)


@mcp.tool()
async def list_hpc_jobs(ctx: Context) -> str:
    """
    List all recently submitted HPC jobs and their states.
    """
    # /api/v1/compute/status/{resource_id} should work but has some odd behavior around "historical" currently
    # I think it only looks up very recent jobs. We may need to rethink how handle the job list
    td = timedelta(hours=1)
    sacct_out = await get_s3m_client().bash("TZ=UTC " + shlex.join([
        "sacct", "--json", "--allocations",
        "--starttime", (datetime.now(timezone.utc) - td).strftime("%Y-%m-%dT%H:%M:%S"),
        "--user", f"{settings.hpc_account}_auser",
    ]))
    sacct_jobs = json.loads(sacct_out)["jobs"]

    lines = []
    for job in sacct_jobs:
        if not job["name"].startswith("vista-"):
            continue
        job_id = str(job["job_id"])
        state = job["state"]["current"][0]
        lines.append(f"{job_id} {state}")

    return "\n".join(lines) if lines else "No jobs found."
