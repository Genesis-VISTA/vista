"""
MCP for remote HPC job submission.
"""

from __future__ import annotations
import json
import re
import os
import itertools
import textwrap
import time
import dataclasses
import logging
from pathlib import Path
import shlex
from fastmcp import FastMCP, Context
from .config import settings
from .lib.ssh import ssh_bash_retry, scp_retry, get_ssh_conn
from .lib.misc import parse_time_limit, validate_job_id, get_tool_call_string

@dataclasses.dataclass
class JobInfo:
    slurm_script: Path
    description: str

def get_available_jobs() -> dict[str, JobInfo]:
    jobs = {}
    for file in settings.local_hpc_jobs_dir.iterdir():
        if file.is_dir():
            slurm_scripts = list(file.glob("*.slurm"))
            if len(slurm_scripts) != 1:
                raise ValueError(f"Expected to find one .slurm script in {file}, found {len(slurm_scripts)}")
            slurm_script = slurm_scripts[0]
            readme = file / "README.md"
            if not readme.exists():
                raise ValueError(f"No README.md in {file}")
            description = readme.read_text().strip()
            if not description.startswith(f"# {file.name}"):
                raise ValueError(f'Job README.md should start with "# {file.name}" header')
            jobs[file.name] = JobInfo(
                slurm_script=slurm_script.relative_to(file),
                description=description,
            )
    return jobs


AVAILABLE_JOBS = get_available_jobs()


def build_job_descriptions() -> str:
    return '\n\n\n'.join(info.description for name, info in AVAILABLE_JOBS.items())


MAX_NODES = 64
MAX_TIME = "4:00:00"


mcp = FastMCP("Submit Job")


@mcp.tool(
    description=textwrap.dedent(f"""
        Submit a Slurm job to the HPC system.

        Args:
            job: The name of the job to run (available jobs: {' '.join(AVAILABLE_JOBS.keys())})
            nodes: Number of nodes for the job (max: {MAX_NODES})
            time_limit: Time limit for the job in "h:mm:ss" format (max: {MAX_TIME})
            script_args: Extra arguments to pass to the script

        Returns:
            The slurm job id.

        Available Jobs:

        {textwrap.indent(build_job_descriptions(), '        ').strip()}
    """).strip(),
)
async def submit_hpc_job(
    ctx: Context,
    job: str,
    nodes: int | None = None,
    time_limit: str | None = None,
    script_args: str | None = None,
) -> str:
    if job not in AVAILABLE_JOBS:
        raise ValueError(f"{job} is not recognized, should be one of: {' '.join(AVAILABLE_JOBS)}")
    if nodes and nodes > MAX_NODES:
        raise ValueError(f"To many nodes specified (max {MAX_NODES})")
    if time_limit and parse_time_limit(time_limit) > parse_time_limit(MAX_TIME):
        raise ValueError(f"Time limit to large (max: {MAX_TIME})")

    ssh_conn = await get_ssh_conn(ctx,
        message = get_tool_call_string("submit_hpc_job",
            job = job,
            nodes = nodes,
            time_limit = time_limit,
            script_args = script_args,
        ),
        force_confirmation = True,
    )

    setup_script_path = settings.remote_hpc_jobs_dir / settings.session_id / 'setup.sh'
    remote_job_dir = settings.remote_hpc_jobs_dir / settings.session_id / job
    check_result = await ssh_bash_retry(ssh_conn, f'[ -d {shlex.quote(str(remote_job_dir))} ] && echo true || echo false"')
    if check_result.strip() != "true":
        await ssh_bash_retry(ssh_conn, f'mkdir -p {shlex.quote(str(remote_job_dir.parent))}')
        setup_script = settings.get_hpc_setup_script()
        await ssh_bash_retry(ssh_conn, f'echo {shlex.quote(setup_script)} > {shlex.quote(str(setup_script_path))}')
        await scp_retry(str(settings.local_hpc_jobs_dir / job), (ssh_conn, str(remote_job_dir)))
        logging.info(f"Synced job to {remote_job_dir}")
        # TODO: should clean up the job script eventually, but don't want to do it on close as we may
        # want to leave jobs running between sessions. Probably best would be to periodically delete
        # completed jobs from old sessions in the dir.

    remote_job_script =  remote_job_dir / AVAILABLE_JOBS[job].slurm_script
    args = ["sbatch"]
    args.extend(["--chdir", str(remote_job_script.parent)])
    if nodes:
        args.extend(['-N', str(nodes)])
    if time_limit:
        args.extend(["-t", time_limit])
    args.extend(["--export", f"ALL,VISTA_SETUP_SCRIPT={setup_script_path}"])
    args.extend(["-o", f"{settings.remote_hpc_jobs_dir}/out/%j/log.out"])
    args.extend(["-J", f"vista-{job}"])
    args.append(str(remote_job_script))
    if script_args:
        args.extend(shlex.split(script_args))

    result = await ssh_bash_retry(ssh_conn, args)

    match = re.search(r"submitted batch job (\d+)", result.lower())
    if match:
        result = match[1]
        logging.info(f"Submitted job {result} to {settings.hpc_host[-1]}")
    else:
        raise ValueError("Job failed to launch: " + result)

    return result


@mcp.tool()
async def get_hpc_job_status(ctx: Context, job_id: str) -> str:
    """
    Get the status and logs of a submitted Slurm job.

    Args:
        job_id: The slurm job id

    """
    job_id = validate_job_id(job_id)

    ssh_conn = await get_ssh_conn(ctx,
        message = get_tool_call_string("get_hpc_job_status", job_id = job_id),
    )

    sacct_out = await ssh_bash_retry(ssh_conn, f"sacct --json -j {shlex.quote(job_id)} --user $USER")
    try:
        sacct_jobs = json.loads(sacct_out)["jobs"]
    except:
        raise ValueError("Malformed sacct output")
    if len(sacct_jobs) <= 0 or not sacct_jobs[0]['name'].startswith("vista-"):
        raise ValueError(f"No job {job_id} found")

    metadata = {
        "JOB_ID": job_id,
        "STATE": sacct_jobs[0]['state']['current'][0],
    }

    output_dir = settings.remote_hpc_jobs_dir / "out" / job_id

    log_path = output_dir / "log.out"
    logs = await ssh_bash_retry(ssh_conn, f"cat {shlex.quote(str(log_path))} 2>/dev/null || true")

    excludes = ['**/.venv*/*', '**/__pycache__/*']
    find_result = await ssh_bash_retry(ssh_conn, shlex.join([
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
        job_id: The slurm job id
        files: List of file paths to download. Relative to the jobs output directory (as shown by get_hpc_job_status).

    Returns:
        The downloaded file paths.
    """
    job_id = validate_job_id(job_id)

    ssh_conn = await get_ssh_conn(ctx,
        message = get_tool_call_string("get_hpc_job_outputs", job_id = job_id, files = files),
    )

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
        await scp_retry((ssh_conn, remote_path), str(local_path))
        downloaded.append(str(sandbox_path))

    return "Downloaded files:\n" + "\n".join(downloaded)


@mcp.tool()
async def list_hpc_jobs(ctx: Context) -> str:
    """
    List all submitted HPC jobs.
    """
    ssh_conn = await get_ssh_conn(ctx,
        message = get_tool_call_string("list_hpc_jobs"),
    )
    sacct_out = await ssh_bash_retry(ssh_conn, "sacct --json --allocations --user $USER")
    try:
        sacct_jobs = json.loads(sacct_out)["jobs"]
    except Exception:
        raise ValueError("Malformed sacct output")

    cutoff = time.time() - 24 * 60 * 60

    lines = []
    for job in sacct_jobs:
        if not job["name"].startswith("vista-"):
            continue
        job_id = str(job["job_id"])

        state = job["state"]["current"][0]
        end_time = job.get("time", {}).get("end", 0)
        if end_time and end_time < cutoff:
            continue

        lines.append(f"{job_id} {state}")

    return "\n".join(lines) if lines else "No jobs found."
