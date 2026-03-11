"""
MCP for remote HPC job submission.
"""

from __future__ import annotations
import getpass
import json
import sys
import re
import subprocess
import asyncssh
import textwrap
from datetime import timedelta
from pathlib import Path
import shlex
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from .config import settings

HOST = "frontier.olcf.ornl.gov"

ssh_conn: asyncssh.SSHClientConnection | None = None


def prompt_credentials() -> tuple[str, str]:
    username = settings.hpc_username
    password = settings.hpc_password
    if not username or not password:
        # use sys.stderr to avoid issues when running MCP on stdio
        print(f"\nSSH login required for {HOST}\n", file=sys.stderr, end="")
    if not username:
        print(f"Username: ", file=sys.stderr, end="")
        username = input()
    if not password:
        password = getpass.getpass(prompt="Password: ", stream=sys.stderr)
    return username, password

def parse_time_limit(s: str):
    """Parse a time delta string in 'h:mm:ss' format."""
    try:
        hours, minutes, seconds = s.split(":")
        return timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds))
    except:
        raise ValueError(f"Invalid time limit: {s}")

async def remote_bash(*args: str) -> str:
    """
    Run a bash command on the remote HPC system
    """
    if ssh_conn is None:
        raise RuntimeError("SSH connection is not available.")

    result = await ssh_conn.run(shlex.join(args), check=False,
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
    )
    return result.stdout


def get_available_jobs() -> dict[str, Path]:
    jobs = {}
    for file in settings.local_hpc_jobs_dir.iterdir():
        if file.is_dir():
            slurm_scripts = list(file.glob("*.slurm"))
            if len(slurm_scripts) != 1:
                raise ValueError(f"Expected to find one .slurm script in {file}, found {len(slurm_scripts)}")
            slurm_script = slurm_scripts[0]
            jobs[file.name] = slurm_script.relative_to(settings.local_hpc_jobs_dir)
    return jobs

@lifespan
async def app_lifespan(server):
    global ssh_conn

    username, password = prompt_credentials()

    ssh_conn = await asyncssh.connect(
        HOST,
        username=username, password=password,
        known_hosts=None, # TODO
    )

    print(f"Connected to {HOST}", file=sys.stderr)

    await ssh_conn.run(
        f'rm -rf {shlex.quote(str(settings.remote_hpc_jobs_dir))} && ' + 
        f'mkdir -p {shlex.quote(str(settings.remote_hpc_jobs_dir.parent))}',
        check=True,
    )
    await asyncssh.scp(
        str(settings.local_hpc_jobs_dir),
        (ssh_conn, str(settings.remote_hpc_jobs_dir)),
        recurse=True,
    )
    print(f"Synced jobs to {settings.remote_hpc_jobs_dir}", file=sys.stderr)

    try:
        yield
    finally:
        ssh_conn.close()


mcp = FastMCP(name="Submit Job", lifespan=app_lifespan)

AVAILABLE_JOBS = get_available_jobs()
MAX_NODES = 64
MAX_TIME = "4:00:00"

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
    """),
)
async def submit_job(
    job: str,
    nodes: int | None = None,
    time_limit: str | None = None,
    script_args: str = "",
) -> str:
    if job not in AVAILABLE_JOBS:
        raise ValueError(f"{job} is not recognized, should be one of: {' '.join(AVAILABLE_JOBS)}")
    if nodes and nodes > MAX_NODES:
        raise ValueError(f"To many nodes specified (max {MAX_NODES})")
    if time_limit and parse_time_limit(time_limit) > parse_time_limit(MAX_TIME):
        raise ValueError(f"Time limit to large (max: {MAX_TIME})")

    remote_job_script = settings.remote_hpc_jobs_dir / AVAILABLE_JOBS[job]

    args = ["sbatch"]
    args.extend(["--chdir", str(remote_job_script.parent)])
    if nodes:
        args.extend(['-N', str(nodes)])
    if time_limit:
        args.extend(["-t", time_limit])
    args.extend(["-o", f"{settings.remote_hpc_jobs_dir}/logs/slurm-%j.out"])
    args.append(str(remote_job_script))
    args.extend(shlex.split(script_args))

    result = await remote_bash(*args)

    match = re.search(r"submitted batch job (\d+)", result.lower())
    if match:
        result = match[1]
    else:
        raise ValueError("Job failed to launch: " + result)

    return result


@mcp.tool()
async def get_job_status(job_id: str) -> str:
    """Get the status and logs of a submitted Slurm job."""
    sacct_out = await remote_bash("sacct", "--json", "-j", job_id)
    try:
        sacct_jobs = json.loads(sacct_out)["jobs"]
    except:
        raise ValueError("Malformed sacct output")
    if len(sacct_jobs) <= 0:
        raise ValueError(f"No job {job_id} found")

    state = sacct_jobs[0]['state']['current'][0]
    log_path = f"{settings.remote_hpc_jobs_dir}/logs/slurm-{job_id}.out"
    logs = await remote_bash("bash", "-c", f"cat {shlex.quote(log_path)} 2>/dev/null || true")

    return f"JOB ID: {job_id}\nSTATE: {state}\nLOGS:\n{logs}"
